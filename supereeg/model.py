from __future__ import division
from __future__ import print_function
import time
import copy
import warnings
import six
import pandas as pd
import numpy as np
import seaborn as sns
import deepdish as dd
import matplotlib.pyplot as plt
from .helpers import _get_corrmat, _r2z, _z2r, _log_rbf, _blur_corrmat, _plot_borderless,\
    _near_neighbor, _timeseries_recon, _count_overlapping, _plot_locs_connectome, _plot_locs_hyp, _gray, _nifti_to_brain,\
    _unique, _union, _empty, _to_log_complex, _to_exp_real
from .brain import Brain
from .nifti import Nifti
from scipy.spatial.distance import cdist

class Model(object):
    """
    supereeg model and associated locations

    This class holds your supereeg model.  To create an instance, pass a list
    of brain objects and the model will be generated from those brain objects.
    You can also add your own model by passing a numpy array as your matrix and
    the corresponding locations. Alternatively, you can bypass creating a
    new model by passing numerator, denominator, locations, and n_subs
    (see parameters for details).  Additionally, you can include a meta dictionary
    with any other information that you want to save with the model.

    Parameters
    ----------

    data : supereeg.Brain or list supereeg.Brain, supereeg.Nifti or list supereeg.Nifti, or Numpy.ndarray

        A supereeg.Brain object or supereeg.Nifti object,  list of objects, or a Numpy.ndarray of your model.

    locs : pandas.DataFrame or np.ndarray
        MNI coordinate (x,y,z) by number of electrode df containing electrode locations

    template : filepath
        Path to a template nifti file used to set model locations

    numerator : Numpy.ndarray
        (Optional) A locations x locations matrix comprising the sum of the log z-transformed
        correlation matrices over subjects.  If used, must also pass denominator,
        locs and n_subs. Otherwise, numerator will be computed from the brain
        object data.

    denominator : Numpy.ndarray
        (Optional) A locations x locations matrix comprising the sum of the log (weighted) number of
        subjects contributing to each matrix cell. If used, must also pass numerator,
        locs and n_subs. Otherwise, denominator will be computed from the brain
        object data.

    n_subs : int
        The number of subjects used to create the model.  Required if you pass
        numerator/denominator.  Otherwise computed automatically from the data.

    rbf_width : positive scalar
        The width of of the radial basis function (RBF) used as a spatial prior for
        smoothing estimates at nearby locations.  (Default: 20)

    meta : dict
        Optional dict containing whatever you want

    date created : str
        Time created

    save : None
        Optional filename to save created model


    Attributes
    ----------
    numerator : Numpy.ndarray
        A locations x locations matrix comprising the sum of the log z-transformed
        correlation matrices over subjects

    denominator : Numpy.ndarray
        A locations x locations matrix comprising the log sum of the (weighted) number of
        subjects contributing to each matrix cell

    n_subs : int
        Number of subject used to create the model


    Returns
    ----------
    model : supereeg.Model instance
        A model that can be used to infer timeseries from unknown locations

    """
    def __init__(self, data=None, locs=None, template=None,
                 numerator=None, denominator=None,
                 n_subs=None, meta=None, date_created=None, rbf_width=20, save=None):
        from .load import load

        self.locs = None
        self.numerator = None
        self.denominator = None
        self.n_subs = 0
        self.meta = meta
        self.date_created = date_created
        self.rbf_width = float(rbf_width)

        if n_subs is None:
            n_subs = 1

        #expanded_to_locs = False
        if not (data is None):
            if type(data) == list:
                if len(data) == 0:
                    data = None
                else:
                    if not (locs is None):
                        if type(locs) == pd.DataFrame:
                            locs = locs.as_matrix()
                        assert type(locs) == np.array, 'Locations must be either a DataFrame or a numpy array'
                        assert locs.shape[1] == 3, 'Only 3d locations are supported'
                    all_locs = locs
                    for i in range(1, len(data)):
                        if type(data) in (Model, Brain, Nifti):
                            if all_locs is None:
                                all_locs = data[i].get_locs().as_matrix()
                            else:
                                all_locs = np.vstack((all_locs, data[i].get_locs().as_matrix()))
                    locs, loc_inds = _unique(all_locs)

                    self.__init__(data=data[0], locs=locs, template=template, meta=self.meta, rbf_width=self.rbf_width, n_subs=1)
                    for i in range(1, len(data)):
                        self.update(Model(data=data[i], locs=locs, template=template, meta=self.meta, rbf_width=self.rbf_width, n_subs=1))

            if isinstance(data, six.string_types):
                data = load(data)

            if isinstance(data, Nifti):
                data = Brain(data)

            if isinstance(data, Model):
                self.date_created = data.date_created
                self.denominator = data.denominator
                self.locs = data.locs
                self.meta = data.meta
                self.n_subs = data.n_subs
                self.numerator = data.numerator
                self.rbf_width = data.rbf_width
                #self = copy.deepcopy(data)
                n_subs = self.n_subs
            elif isinstance(data, Brain):
                corrmat = _get_corrmat(data)
                self.__init__(data=corrmat, locs=data.get_locs(), n_subs=1)
            elif isinstance(data, np.ndarray):
                assert not (locs is None), 'must specify model locations'
                assert locs.shape[0] == data.shape[0], 'number of locations must match the size of the given correlation matrix'

                self.locs = locs
                self.numerator = _to_log_complex(_r2z(data))
                self.denominator = np.zeros_like(self.numerator, dtype=np.float32)

        if not ((numerator is None) or (denominator is None)):
            assert numerator.shape[0] == numerator.shape[1], 'numerator must be a square matrix'
            assert denominator.shape[0] == denominator.shape[1], 'denominator must be a square matrix'
            assert numerator.shape[0] == denominator.shape[0], 'numerator and denominator must be the same shape'
            assert not (locs is None), 'must specify model locations'
            assert locs.shape[0] == numerator.shape[0], 'number of locations must match the size of the numerator and denominator matrices'

            if (self.numerator is None) or (self.denominator is None):
                self.numerator = numerator
                self.denominator = denominator
            else: #numerator and denominator may have already been inferred data; effectively the user has now passed in *two* sets of data
                self._set_numerator(np.logaddexp(self.numerator.real, numerator.real),
                                    np.logaddexp(self.numerator.imag, numerator.imag))
                self.denominator = np.logaddexp(self.denominator, denominator)

            self.locs = locs
            self.n_subs += n_subs

        if not (template is None): #blur correlation matrix out to template locations
            if not (locs is None):
                warnings.warn('Argument ''locs'' will be ignored in favor of the provided Nifti template')
            if isinstance(template, six.string_types):
                template = load(template)
            assert type(template) == Nifti, 'template must be a Nifti object or a path to a Nifti object'
            bo = Brain(template)
            rbf_weights = _log_rbf(bo.get_locs(), self.locs, width=self.rbf_width)
            self.numerator, self.denominator = _blur_corrmat(self.get_model(z_transform=True), rbf_weights)
            self.locs = bo.get_locs()
        elif not (locs is None): #blur correlation matrix out to locs
            if (isinstance(data, Brain) or isinstance(data, Model)): #self.locs may now conflict with locs
                if not ((locs.shape[0] == self.locs.shape[0]) and np.allclose(locs, self.locs)):
                    rbf_weights = _log_rbf(locs, self.locs, width=self.rbf_width)
                    self.numerator, self.denominator = _blur_corrmat(self.get_model(z_transform=True), rbf_weights)
                    self.locs = locs
        elif self.locs is None:
            self.locs = locs

        assert not (self.locs is None), 'Must specify model locations directly via locs argument, or indirectly via a Model or Brain object (or both)'

        #sort locations and force them to be unique
        self.locs, loc_inds = _unique(self.locs)
        self.numerator = self.numerator[loc_inds, :][:, loc_inds]
        self.denominator = self.denominator[loc_inds, :][:, loc_inds]
        self.n_locs = self.locs.shape[0]

        if not type(self.locs) == pd.DataFrame:
            self.locs = pd.DataFrame(data=self.locs, columns=['x', 'y', 'z'])

        if not self.date_created:
            self.date_created = time.strftime("%c")

        self.n_locs = self.locs.shape[0]
        self.n_subs = n_subs

        if not (save is None):
            if type(save) == str:
                self.save(save)
            else:
                warnings.warn('bad filename, cannot save to disk: ' + str(save))

    def get_model(self, z_transform=False):
        """ Returns a copy of the model in the form of a correlation matrix"""
        if (self.numerator is None) or (self.denominator is None):
            m = np.eye(self.n_locs)
        else:
            m = _recover_model(self.numerator, self.denominator, z_transform=z_transform)
            m[np.isnan(m)] = 0
        return m

    def get_locs(self):
        """ Returns the locations in the model
        """
        return self.locs

    def set_locs(self, new_locs, include_original_locs=False):
        """
        update self.locs to a new set of locations (and blur the correlation matrix accordingly).  if
        include_original_locs is True (default: False), the final set of locations will also include the old locations.
        """
        if include_original_locs:
            new_locs = _union(self.locs, new_locs)
        else:
            new_locs, tmp = _unique(new_locs)

        if _empty(self.locs):
            self.locs = new_locs
            if not _empty(new_locs):
                self.numerator = np.log(self.zeros([new_locs.shape[0], new_locs.shape[0]], dtype=np.complex128))
                self.denominator = np.zeros_like(self.numerator, dtype=np.float64)
                self.locs = new_locs
                self.n_locs = new_locs.shape[0]
            return
        elif _empty(new_locs):
            if not include_original_locs:
                self.locs = pd.DataFrame(columns=('x', 'y', 'z'))
                self.n_locs = 0
                self.numerator = np.array([], dtype=np.complex128)
                self.denominator = np.array([], dtype=np.float64)
            return

        new_locs_in_self = _count_overlapping(self.get_locs(), new_locs)

        if np.all(new_locs_in_self):
            self.locs = self.locs.iloc[new_locs_in_self, :]
            self.n_locs = self.locs.shape[0]
            self.numerator = self.numerator[new_locs_in_self, :][:, new_locs_in_self]
            self.denominator = self.denominator[new_locs_in_self, :][:, new_locs_in_self]
            return
        else:
            rbf_weights = _log_rbf(new_locs, self.get_locs())
            self.numerator, self.denominator = _blur_corrmat(self.get_model(z_transform=True), rbf_weights)
            self.locs = new_locs

        self.locs, loc_inds = _unique(self.locs)
        self.numerator = self.numerator[loc_inds, :][:, loc_inds]
        self.denominator = self.denominator[loc_inds, :][:, loc_inds]
        self.n_locs = self.locs.shape[0]



    def predict(self, bo, nearest_neighbor=False, match_threshold='auto',
                force_update=False, update_locs=True, preprocess='zscore'):
        """
        Takes a brain object and a 'full' covariance model, fills in all
        electrode timeseries for all missing locations and returns the new brain
        object

        Parameters
        ----------
        bo : a Brain, Nifti, or Model object that will be converted to a Brain object.

        nearest_neighbor : True
            Default finds the nearest voxel for each subject's electrode
            location and uses that as revised electrodes location matrix in the
            prediction.

        match_threshold : 'auto' or int
            auto: if match_threshold auto, ignore all electrodes whose distance
            from the nearest matching voxel is greater than the maximum voxel
            dimension

            If value is greater than 0, inlcudes only electrodes that are within
            that distance of matched voxel

        force_update : False
            If True, will update model with patient's correlation matrix.

        update_locs : True
            If True, and if force_update = False, update the locations in the model to include the locations in the
            given brain object prior to generating the predictions.  If force_update = True, this parameter is
            forced to be True (force_update requires updating the locations) and the specified value is ignored.

        preprocess : 'zscore' or None
            The predict algorithm requires the data to be zscored.  However, if
            your data are already zscored you can bypass this by setting to None.

        Returns
        ----------
        bo_p : supereeg.Brain
            New brain data object with missing electrode locations filled in

        """

        if not (type(bo) == Brain):
            bo = Brain(bo)

        bo = bo.get_filtered_bo()

        # if match_threshold auto, ignore all electrodes whose distance from the
        # nearest matching voxel is greater than the maximum voxel dimension
        if nearest_neighbor:
            bo = _near_neighbor(bo, self, match_threshold=match_threshold)

        if self.locs.shape[0] > 1000:
            warnings.warn('Model locations exceed 1000, this may take a while. Go grab a cup of coffee.')

        # if True will update the model with subject's correlation matrix
        if force_update:
            mo = self.update(bo, inplace=False)
        else:
            mo = self

        #blur out model to include brain object locations
        mo.set_locs(bo.get_locs(), include_original_locs=True)

        activations = _timeseries_recon(bo, mo, preprocess=preprocess)
        return Brain(data=activations, locs=mo.locs, sessions=bo.sessions, sample_rate=bo.sample_rate)

        # bool_mask = _count_overlapping(self, bo)
        #
        #
        # case = _which_case(bo, bool_mask)
        # if case is 'all_overlap':
        #     d = cdist(bo.get_locs(), self.locs)
        #     joint_bo_inds = np.where(np.isclose(d, 0))[0]
        #     bo.locs = bo.locs.iloc[joint_bo_inds]
        #     bo.data = bo.data[joint_bo_inds]
        #     bo.kurtosis = bo.kurtosis[joint_bo_inds]
        #     bo.label = np.array(bo.label)[joint_bo_inds].tolist()
        #
        #     return Brain(data=bo.data, locs=bo.locs, sessions=bo.sessions, sample_rate=bo.sample_rate)
        # else:
        #     # indices of the mask (where there is overlap
        #     joint_model_inds = np.where(bool_mask)[0]
        #     if case is 'no_overlap':
        #         model_corrmat_z, loc_label, perm_locs = _no_overlap(self, bo, model_corrmat_z, width=self.rbf_width)
        #     elif case is 'some_overlap':
        #         model_corrmat_z, loc_label, perm_locs = _some_overlap(self, bo, model_corrmat_z, joint_model_inds, width=self.rbf_width)
        #     elif case is 'subset':
        #         model_corrmat_z, loc_label, perm_locs = _subset(self, bo, model_corrmat_z, joint_model_inds)
        #
        #     model_corrmat_z = _z2r(model_corrmat_z)
        #     #np.fill_diagonal(model_corrmat_x, 0) #according to Lucy's latest explorations, we should *not* fill the diagonal with zeros
        #     activations = _timeseries_recon(bo, model_corrmat_z, preprocess=preprocess)
        #
        #     return Brain(data=activations, locs=perm_locs, sessions=bo.sessions,
        #                 sample_rate=bo.sample_rate, kurtosis=None, label=loc_label)

    def update(self, data, inplace=True):
        """
        Update a model with new data.

        Parameters
        ----------
        data : supereeg.Brain, supereeg.Nifti, supereeg.Model (or a mixed list of these)
            New data

        inplace : bool
            Whether to run update in place or return a new model (default True).

        Returns
        ----------
        model : supereeg.Model
            A new updated model object

        """
        if inplace:
            m1 = self
        else:
            m1 = Model(self)

        m2 = Model(data)
        locs = _union(m1.get_locs(), m2.get_locs())

        m1.set_locs(locs)
        m2.set_locs(locs)

        m1._set_numerator(np.logaddexp(m1.numerator.real, m2.numerator.real),
                          np.logaddexp(m1.numerator.imag, m2.numerator.imag))
        m1.denominator = np.logaddexp(m1.denominator, m2.denominator)
        m1.locs = locs
        m1.n_locs = locs.shape[0]
        m1.n_subs += m2.n_subs

        #combine meta info
        if not ((m1.meta is None) and (m2.meta is None)):
            if m1.meta is None:
                m1.meta = m2.meta
            elif (type(m1.meta) == dict) and (type(m2.meta) == dict):
                m1.meta.update(m2.meta)

        if not inplace:
            return m1


    def _set_numerator(self, n_real, n_imag):
        """
        Internal function for setting the numerator (deals with size mismatches)
        """
        self.numerator = np.zeros_like(n_real, dtype=np.complex128)
        self.numerator.real = n_real
        self.numerator.imag = n_imag


    def info(self):
        """
        Print info about the model object

        Prints the number of electrodes, number of subjects, date created,
        and any optional meta data.
        """
        print('Number of locations: ' + str(self.n_locs))
        print('Number of subjects: ' + str(self.n_subs))
        print('RBF width: ' + str(self.rbf_width))
        print('Date created: ' + str(self.date_created))
        print('Meta data: ' + str(self.meta))

    def plot_data(self, savefile=None, show=True, **kwargs):
        """
        Plot the supereeg model as a correlation matrix

        This function wraps seaborn's heatmap and accepts any inputs that seaborn
        supports for models less than 2000x2000.  If the model is larger, the plot cannot be
        generated without specifying a savefile.

        Parameters
        ----------
        show : bool
            If False, image not rendered (default : True)

        Returns
        ----------
        ax : matplotlib.Axes
            An axes object

        """

        corr_mat = self.get_model(z_transform=False)

        if np.shape(corr_mat)[0] < 2000:
            ax = sns.heatmap(corr_mat, cbar_kws = {'label': 'correlation'}, **kwargs)
        else:
            if savefile == None:
                raise NotImplementedError('Cannot plot large models when savefile is None')
            else:
                ax = _plot_borderless(corr_mat, savefile=savefile, vmin=-1, vmax=1, cmap='Spectral')
        if show:
            plt.show()

        return ax

    def plot_locs(self, pdfpath=None):
        """
        Plots electrode locations from brain object


        Parameters
        ----------
        pdfpath : str
        A name for the file.  If the file extension (.pdf) is not specified, it will be appended.

        """

        locs = self.locs
        if self.locs .shape[0] <= 10000:
            _plot_locs_connectome(locs, pdfpath)
        else:
            _plot_locs_hyp(locs, pdfpath)

    def save(self, fname, compression='blosc'):
        """
        Save method for the model object

        The data will be saved as a 'mo' file, which is a dictionary containing
        the elements of a model object saved in the hd5 format using
        `deepdish`.

        Parameters
        ----------
        fname : str
            A name for the file.  If the file extension (.mo) is not specified,
            it will be appended.

        compression : str
            The kind of compression to use.  See the deepdish documentation for
            options: http://deepdish.readthedocs.io/en/latest/api_io.html#deepdish.io.save

        """

        mo = {
            'numerator' : self.numerator,
            'denominator' : self.denominator,
            'locs' : self.locs,
            'n_subs' : self.n_subs,
            'meta' : self.meta,
            'date_created' : self.date_created,
            'rbf_width' : self.rbf_width
        }

        if fname[-3:]!='.mo':
            fname+='.mo'

        dd.io.save(fname, mo, compression=compression)

    def get_slice(self, inds, inplace=False):
        """
        Indexes model object data

        Parameters
        ----------
        inds : scalar, list, or numpy array
            locations you wish to index (relative to model.get_locs())

        inplace : bool
            If True, indexes in place; otherwise a new Model object is returned
            (default: False)

        """
        numerator = self.numerator[inds][:, inds]
        denominator = self.denominator[inds][:, inds]
        locs = self.locs.iloc[inds]
        n_subs = self.n_subs
        meta = self.meta
        date_created = time.strftime("%c")

        if inplace:
            self.numerator = numerator
            self.denominator = denominator
            self.locs = locs
            self.n_subs = n_subs
            self.meta = meta
            self.date_created = date_created
        else:
            return Model(numerator=numerator, denominator=denominator, locs=locs,
                         n_subs=n_subs, meta=meta, date_created=date_created, rbf_width=self.rbf_width)

    def __add__(self, other):
        """
        Add two model objects together. The models must have matching
        locations.  Meta properties are combined across objects, or if properties
        conflict then the values from the first object are preferred.

        Parameters
        ----------
        other: Model object to be added to the current object
        """

        return self.update(other, inplace=False)

    # #subtraction is not working; removing functionality until fixed
    # def __sub__(self, other):
    #     """
    #     Subtract one model object from another. The models must have matching
    #     locations.  Meta properties are combined across objects, or if properties
    #     conflict then the values from the first object are preferred.
    #
    #     Parameters
    #     ----------
    #     other: Model object to be subtracted from the current object
    #     """
    #
    #     if type(other) == Brain:
    #         d = Model(other, locs=other.get_locs())
    #     elif type(other) == Nifti:
    #         d = Model(other)
    #     elif type(other) == Model:
    #         d = Model(other, locs=other.locs) #make a copy
    #     else:
    #         raise Exception('Unsupported data type for subtraction from Model object: ' + str(type(other)))
    #
    #     def logdiffexp(a, b):
    #         return np.add(a, np.log(np.subtract(1, np.exp(np.subtract(b, a)))))
    #
    #     assert np.allclose(self.locs, other.locs), 'subtraction is only supported for models with matching locations'
    #
    #     m = copy.deepcopy(self)
    #     m.numerator.real = logdiffexp(self.numerator.real, other.numerator.real)
    #     m.numerator.imag = logdiffexp(self.numerator.imag, other.numerator.imag)
    #     m.denominator = logdiffexp(self.denominator, other.denominator)
    #     m.n_subs -= other.n_subs
    #
    #     if m.meta is None:
    #         m.meta = other.meta
    #     elif (type(m.meta) == dict) and (type(other.meta) == dict):
    #         m.meta.update(other.meta)
    #
    #     return m


###################################
# helper functions for init
###################################

def _handle_superuser(self, numerator, denominator, locs, n_subs):
    """Shortcuts model building if these args are passed"""
    self.numerator = numerator
    self.denominator = denominator

    # if locs arent already a df, turn them into df
    if isinstance(locs, pd.DataFrame):
        self.locs = locs
    else:
        self.locs = pd.DataFrame(locs, columns=['x', 'y', 'z'])

    self.n_subs = n_subs

def _create_locs(self, locs, template):
    """get locations from template, or from locs arg"""
    if locs is None:
        if template is None:
            template = _gray(20)
        nii_data, nii_locs, nii_meta = _nifti_to_brain(template)
        self.locs = pd.DataFrame(nii_locs, columns=['x', 'y', 'z'])
    else:
        self.locs = pd.DataFrame(locs, columns=['x', 'y', 'z'])
    if self.locs.shape[0]>1000:
        warnings.warn('Model locations exceed 1000, this may take a while. Go get a cup of coffee or brew some tea!')

def _bo2model(bo, locs, width=20):
    """Returns numerator and denominator given a brain object"""
    sub_corrmat = _get_corrmat(bo)
    #np.fill_diagonal(sub_corrmat, 0)
    sub_corrmat_z = _r2z(sub_corrmat)
    sub_rbf_weights = _log_rbf(locs, bo.get_locs(), width=width)
    n, d = _blur_corrmat(sub_corrmat_z, sub_rbf_weights)
    return n, d, 1

def _mo2model(mo, locs, width=20):
    """Returns numerator and denominator for model object"""

    if not isinstance(locs, pd.DataFrame):
        locs = pd.DataFrame(locs, columns=['x', 'y', 'z'])
    if locs.equals(mo.locs):
        return mo.numerator.copy(), mo.denominator.copy(), mo.n_subs
    else:
        # if the locations are not equivalent, map input model into locs space
        sub_corrmat_z = _recover_model(mo.numerator, mo.denominator, z_transform=True)
        #np.fill_diagonal(sub_corrmat_z, 0)
        sub_rbf_weights = _log_rbf(locs, mo.locs, width=width)
        n, d = _blur_corrmat(sub_corrmat_z, sub_rbf_weights)
        return n, d, mo.n_subs

def _force_update(mo, bo, width=20):
    # get subject-specific correlation matrix
    sub_corrmat = _get_corrmat(bo)

    # fill diag with zeros
    #np.fill_diagonal(sub_corrmat, 0) # <- possible failpoint

    # z-score the corrmat
    sub_corrmat_z = _r2z(sub_corrmat)

    # get _rbf weights
    sub__rbf_weights = _log_rbf(mo.locs, bo.get_locs(), width=width)

    #  get subject expanded correlation matrix
    num_corrmat_x, denom_corrmat_x = _blur_corrmat(sub_corrmat_z, sub__rbf_weights)

    # add in new subj data
    #with np.errstate(invalid='ignore'):
    n = mo.numerator.copy()
    n.real = np.logaddexp(n.real, num_corrmat_x.real)
    n.imag = np.logaddexp(n.imag, num_corrmat_x.imag)
    return _recover_model(n, np.logaddexp(mo.denominator, denom_corrmat_x), z_transform=True)

# ###################################
# # helper functions for predict
# ###################################
#
# def _which_case(bo, bool_mask):
#     """Determine which predict scenario we are in"""
#     if all(bool_mask):
#         return 'all_overlap'
#     if not any(bool_mask):
#         return 'no_overlap'
#     elif sum(bool_mask) == bo.get_locs().shape[0]:
#         return 'subset'
#     elif sum(bool_mask) != bo.get_locs().shape[0]:
#         return 'some_overlap'
#
#
#
#
# def _no_overlap(self, bo, model_corrmat_x, width=20):
#     """ Compute model when there is no overlap """
#
#     # expanded _rbf weights
#     model__rbf_weights = _log_rbf(pd.concat([self.locs, bo.get_locs()]), self.locs, width=width)
#
#     # get model expanded correlation matrix
#     num_corrmat_x, denom_corrmat_x = _blur_corrmat(model_corrmat_x, model__rbf_weights)
#
#     # divide the numerator and denominator
#     #with np.errstate(invalid='ignore'):
#     model_corrmat_x = _recover_model(num_corrmat_x, denom_corrmat_x, z_transform=True)
#
#     # label locations as reconstructed or observed
#     loc_label = ['reconstructed'] * len(self.locs) + ['observed'] * len(bo.get_locs())
#
#     # grab the locs
#     perm_locs = self.locs.append(bo.get_locs())
#
#     return model_corrmat_x, loc_label, perm_locs
#
# def _subset(self, bo, model_corrmat_x, joint_model_inds):
#     """ Compute model when bo is a subset of the model """
#     # permute the correlation matrix so that the inds to reconstruct are on the right edge of the matrix
#     perm_inds = sorted(set(range(self.locs.shape[0])) - set(joint_model_inds)) + sorted(set(joint_model_inds))
#     model_corrmat_x = model_corrmat_x[:, perm_inds][perm_inds, :]
#
#     # label locations as reconstructed or observed
#     loc_label = ['reconstructed'] * (len(self.locs)-len(bo.get_locs())) + ['observed'] * len(bo.get_locs())
#
#     # grab permuted locations
#     perm_locs = self.locs.iloc[perm_inds]
#
#     return model_corrmat_x, loc_label, perm_locs
#
# def _some_overlap(self, bo, model_corrmat_x, joint_model_inds, width=20):
#     """ Compute model when there is some overlap """
#
#     # get subject indices where subject locs do not overlap with model locs
#
#     bool_bo_mask= np.sum([(bo.get_locs() == y).all(1) for idy, y in self.locs.iterrows()], 0).astype(bool)
#     disjoint_bo_inds = np.where(~bool_bo_mask)[0]
#     # d = cdist(bo.get_locs(), self.locs)
#     # disjoint_bo_inds = np.where(np.isclose(d, 0))[0]
#
#     # permute the correlation matrix so that the inds to reconstruct are on the right edge of the matrix
#     perm_inds = sorted(set(range(self.locs.shape[0])) - set(joint_model_inds)) + sorted(set(joint_model_inds))
#     model_permuted = model_corrmat_x[:, perm_inds][perm_inds, :]
#
#     # permute the model locations (important for the _rbf calculation later)
#     model_locs_permuted = self.locs.iloc[perm_inds]
#
#     # permute the subject locations arranging them
#     bo_perm_inds = sorted(set(range(bo.get_locs().shape[0])) - set(disjoint_bo_inds)) + sorted(set(disjoint_bo_inds))
#     sub_bo = bo.get_locs().iloc[disjoint_bo_inds]
#
#     #FIXME: won't this change the brain object that the user passes in?  seems problematic...do we need to copy it first?
#     bo = copy.deepcopy(bo) #added this line re: FIXME statement...
#     bo.locs = bo.locs.iloc[bo_perm_inds]
#     bo.data = bo.data[bo_perm_inds]
#     bo.kurtosis = bo.kurtosis[bo_perm_inds]
#
#     # permuted indices for unknown model locations
#     perm_inds_unknown = sorted(set(range(self.locs.shape[0])) - set(joint_model_inds))
#     # expanded _rbf weights
#     #model__rbf_weights = _rbf(pd.concat([model_locs_permuted, bo.locs]), model_locs_permuted)
#     model__rbf_weights = _log_rbf(pd.concat([model_locs_permuted, sub_bo]), model_locs_permuted, width=width)
#
#     # get model expanded correlation matrix
#     num_corrmat_x, denom_corrmat_x = _blur_corrmat(model_permuted, model__rbf_weights)
#
#     # divide the numerator and denominator
#     #with np.errstate(invalid='ignore'):
#     model_corrmat_x = _recover_model(num_corrmat_x, denom_corrmat_x, z_transform=True)
#
#     # add back the permuted correlation matrix for complete subject prediction
#     model_corrmat_x[:model_permuted.shape[0], :model_permuted.shape[0]] = model_permuted
#
#     # label locations as reconstructed or observed
#     loc_label = ['reconstructed'] * len(self.locs.iloc[perm_inds_unknown]) + ['observed'] * len(bo.get_locs())
#
#     ## unclear if this will return too many locations
#     perm_locs = self.locs.iloc[perm_inds_unknown].append(bo.get_locs())
#
#     return model_corrmat_x, loc_label, perm_locs

def _recover_model(num, denom, z_transform=False):
    warnings.simplefilter('ignore')

    m = np.divide(_to_exp_real(num), np.exp(denom)) #numerator and denominator are in log units
    if z_transform:
        np.fill_diagonal(m, np.inf)
        return m
    else:
        np.fill_diagonal(m, 1)
        return _z2r(m)