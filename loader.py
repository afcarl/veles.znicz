"""
Created on Aug 14, 2013

Loader base class.

Copyright (c) 2013 Samsung Electronics Co., Ltd.
"""

from __future__ import division

import numpy
import os
import time
from zope.interface import implementer, Interface

import veles.config as config
from veles.distributable import IDistributable
import veles.error as error
import veles.formats as formats
from veles.mutable import Bool
import veles.opencl_types as opencl_types
import veles.random_generator as rnd
from veles.units import Unit, IUnit


TRAIN = 2
VALID = 1
TEST = 0
TRIAGE = {"train": TRAIN,
          "validation": VALID,
          "valid": VALID,
          "test": TEST}
CLASS_NAME = ["test", "validation", "train"]


class LoaderError(Exception):
    pass


class ILoader(Interface):
    def load_data():
        """Load the data here.

        Should be filled here:
            class_samples[].
        """

    def create_minibatches():
        """Allocate arrays for minibatch_data etc. here.
        """

    def fill_minibatch():
        """Fill minibatch data labels and indexes according to current shuffle.
        """


@implementer(IUnit, IDistributable)
class Loader(Unit):
    """Loads data and provides mini-batch output interface.

    Attributes:
        rnd: rnd.Rand().

        minibatch_data: data (should be scaled usually scaled to [-1, 1]).
        minibatch_indexes: global indexes of images in minibatch.
        minibatch_labels: labels for indexes in minibatch
                          (in case of classification task).
        minibatch_target: target data (in case of MSE).
        class_target: target for each class
                      (in case of classification with MSE).

        minibatch_class: class of the minibatch: 0-test, 1-validation, 2-train.
        last_minibatch: if current minibatch is last in it's class.

        minibatch_offset: offset of the current minibatch in all samples,
                        where first come test samples, then validation, with
                        train ones at the end.
        minibatch_size: size of the current minibatch.
        minibatch_maxsize: maximum size of minibatch in samples.

        total_samples: total number of samples in the dataset.
        class_samples: number of samples per class.
        nextclass_offsets: offset in samples where the next class begins.
        normalize: normalize pixel values to [-1, 1] range. True by default.

        shuffled_indexes: indexes for all dataset, shuffled with rnd.
        epoch_ended: True right after validation is completed and no samples
        have been served since.
        epoch_number: current epoch number.

    Should be overriden in child class:
        load_data()
        create_minibatches()
        fill_minibatch()
    """

    def __init__(self, workflow, **kwargs):
        kwargs["view_group"] = "LOADER"
        super(Loader, self).__init__(workflow, **kwargs)
        self.verify_interface(ILoader)

        self._rnd = [kwargs.get("rnd", rnd.get())]
        self._normalize = kwargs.get("normalize", True)
        self.minibatch_maxsize = kwargs.get("minibatch_maxsize", 100)
        self.validation_ratio = kwargs.get("validation_ratio", 0.15)

        self.minibatch_data = formats.Vector()
        self.minibatch_target = formats.Vector()
        self.minibatch_indexes = formats.Vector()
        self.minibatch_labels = formats.Vector()
        self.class_target = formats.Vector()

        self.minibatch_class = 0
        self.last_minibatch = Bool(False)

        self.total_samples = 0
        self.class_samples = [0, 0, 0]
        self.nextclass_offsets = [0, 0, 0]
        self.minibatch_offset = 0

        self.minibatch_offset = 0
        self.minibatch_size = 0

        self.shuffled_indexes = False  # allow early linking
        self.original_labels = False  # allow early linking

        self.samples_served = 0
        self.epoch_ended = Bool(False)
        self.epoch_number = 0

    def init_unpickled(self):
        super(Loader, self).init_unpickled()
        self._minibatch_serve_timestamp_ = time.time()

    @property
    def rnd(self):
        return self._rnd

    @property
    def normalize(self):
        return self._normalize

    def __getstate__(self):
        state = super(Loader, self).__getstate__()
        state["shuffled_indexes"] = None
        return state

    def initialize(self, **kwargs):
        """Loads the data, initializes indices, shuffles the training set.
        """
        self.minibatch_maxsize = kwargs.get("minibatch_maxsize",
                                            self.minibatch_maxsize)
        self.load_data()

        self._recompute_total_samples()

        self.info("Samples number: train: %d, validation: %d, test: %d",
                  self.class_samples[TRAIN], self.class_samples[VALID],
                  self.class_samples[TEST])

        # Adjust minibatch_maxsize
        self.minibatch_maxsize = min(
            self.minibatch_maxsize, max(self.class_samples[TRAIN],
                                        self.class_samples[VALID],
                                        self.class_samples[TEST]))
        self.info("Minibatch size is set to %d", self.minibatch_maxsize)

        self.create_minibatches()
        if not self.minibatch_data:
            raise error.ErrBadFormat("minibatch_data MUST be initialized in "
                                     "create_minibatches()")

        self.minibatch_offset = self.total_samples

        # Initial shuffle.
        if self.shuffled_indexes is False:
            if self.total_samples > 2147483647:
                raise error.ErrNotImplemented(
                    "total_samples exceedes int32 capacity.")
            self.shuffled_indexes = numpy.arange(self.total_samples,
                                                 dtype=numpy.int32)

        self.shuffle()

    def run(self):
        """Prepares the minibatch.
        """
        self._prepare_next_minibatch()

        # Fill minibatch according to current random shuffle and offset.
        self.minibatch_data.map_invalidate()
        self.minibatch_target.map_invalidate()
        self.minibatch_labels.map_invalidate()
        self.minibatch_indexes.map_invalidate()

        self.fill_minibatch()

        # Fill excessive indexes.
        minibatch_size = self.minibatch_size
        if minibatch_size < self.minibatch_maxsize:
            self.minibatch_data[minibatch_size:] = 0.0
            if self.minibatch_target:
                self.minibatch_target[minibatch_size:] = 0.0
            if self.minibatch_labels:
                self.minibatch_labels[minibatch_size:] = -1
            if self.minibatch_indexes:
                self.minibatch_indexes[minibatch_size:] = -1

    def generate_data_for_master(self):
        data = {"minibatch_class": self.minibatch_class,
                "minibatch_size": self.minibatch_size,
                "minibatch_offset": self.minibatch_offset}
        return data

    def generate_data_for_slave(self, slave):
        self._prepare_next_minibatch()
        data = {'shuffled_indexes':
                self.shuffled_indexes[
                    self.minibatch_offset - self.minibatch_size:
                    self.minibatch_offset].copy(),
                'minibatch_class': self.minibatch_class,
                'minibatch_offset': self.minibatch_offset,
                'samples_served': self.samples_served,
                'epoch_number': self.epoch_number,
                'epoch_ended': bool(self.epoch_ended)}
        return data

    def apply_data_from_master(self, data):
        # Just feed single minibatch
        indices = data['shuffled_indexes']
        assert len(indices) > 0
        self.minibatch_size = len(indices)
        self.minibatch_offset = data['minibatch_offset']
        self.minibatch_class = data['minibatch_class']
        self.samples_served = data['samples_served']
        self.epoch_number = data['epoch_number']
        self.epoch_ended <<= data['epoch_ended']
        assert self.minibatch_offset <= len(self.shuffled_indexes)
        assert (self.minibatch_offset - self.minibatch_size) >= 0
        self.shuffled_indexes[self.minibatch_offset - self.minibatch_size:
                              self.minibatch_offset] = indices
        # these will be incremented back in _prepare_next_minibatch
        self.minibatch_offset -= self.minibatch_size
        self.samples_served -= self.minibatch_size

    def apply_data_from_slave(self, data, slave):
        self.minibatch_class = data["minibatch_class"]
        self.minibatch_size = data["minibatch_size"]
        self.minibatch_offset = data["minibatch_offset"]

    def drop_slave(self, slave):
        pass

    def extract_validation_from_train(self, rand=None):
        """Extracts validation dataset from train dataset randomly.

        We will rearrange indexes only.

        Parameters:
            amount: how many samples move from train dataset
                    relative to the entire samples count for each class.
            rand: rnd.Rand(), if None - will use self.rnd.
        """
        amount = self.validation_ratio
        if rand is None:
            rand = self.rnd[0]

        if amount <= 0:  # Dispose of validation set
            self.class_samples[TRAIN] += self.class_samples[VALID]
            self.class_samples[VALID] = 0
            if self.shuffled_indexes is False:
                total_samples = numpy.sum(self.class_samples)
                self.shuffled_indexes = numpy.arange(
                    total_samples, dtype=numpy.int32)
            return
        offs_test = self.class_samples[TEST]
        offs = offs_test
        train_samples = self.class_samples[VALID] + self.class_samples[TRAIN]
        total_samples = train_samples + offs
        original_labels = self.original_labels

        if self.shuffled_indexes is False:
            self.shuffled_indexes = numpy.arange(
                total_samples, dtype=numpy.int32)
        shuffled_indexes = self.shuffled_indexes

        # If there are no labels
        if original_labels is None:
            n = int(numpy.round(amount * train_samples))
            while n > 0:
                i = rand.randint(offs, offs + train_samples)

                # Swap indexes
                ii = shuffled_indexes[offs]
                shuffled_indexes[offs] = shuffled_indexes[i]
                shuffled_indexes[i] = ii

                offs += 1
                n -= 1
            self.class_samples[VALID] = offs - offs_test
            self.class_samples[TRAIN] = (total_samples
                                         - self.class_samples[VALID]
                                         - offs_test)
            return
        # If there are labels
        nn = {}
        for i in shuffled_indexes[offs:]:
            l = original_labels[i]
            nn[l] = nn.get(l, 0) + 1
        n = 0
        for l in nn.keys():
            n_train = nn[l]
            nn[l] = max(int(numpy.round(amount * nn[l])), 1)
            if nn[l] >= n_train:
                raise error.ErrNotExists("There are too few labels "
                                         "for class %d" % (l))
            n += nn[l]
        while n > 0:
            i = rand.randint(offs, offs_test + train_samples)
            l = original_labels[shuffled_indexes[i]]
            if nn[l] <= 0:
                # Move unused label to the end

                # Swap indexes
                ii = shuffled_indexes[offs_test + train_samples - 1]
                shuffled_indexes[
                    offs_test + train_samples - 1] = shuffled_indexes[i]
                shuffled_indexes[i] = ii

                train_samples -= 1
                continue
            # Swap indexes
            ii = shuffled_indexes[offs]
            shuffled_indexes[offs] = shuffled_indexes[i]
            shuffled_indexes[i] = ii

            nn[l] -= 1
            n -= 1
            offs += 1
        self.class_samples[VALID] = offs - offs_test
        self.class_samples[TRAIN] = (total_samples - self.class_samples[VALID]
                                     - offs_test)

    def shuffle(self):
        """Randomly shuffles the TRAIN dataset.
        """
        self.rnd[0].shuffle(self.shuffled_indexes[self.nextclass_offsets[1]:
                                                  self.nextclass_offsets[2]])

    def _recompute_total_samples(self):
        """Fills self.nextclass_offsets from self.class_samples.
        """
        total_samples = 0
        for i, n in enumerate(self.class_samples):
            total_samples += n
            self.nextclass_offsets[i] = total_samples
        self.total_samples = total_samples
        if total_samples == 0:
            raise error.ErrBadFormat("class_samples should be filled")
        self.last_minibatch <<= False
        self.epoch_ended <<= False

    def _update_epoch_ended(self):
        self.epoch_ended <<= (
            bool(self.last_minibatch) and (
                self.minibatch_class == VALID or (
                    not self.class_samples[VALID] and
                    self.minibatch_class == TRAIN)))

    def _prepare_next_minibatch(self):
        """Increments minibatch_offset by an appropriate minibatch_size.
        """
        # Shuffle again when the end of data is reached.
        if self.minibatch_offset >= self.total_samples:
            self.shuffle()
            self.minibatch_offset = 0

        # Compute next minibatch size and its class.
        if not self.is_slave:
            for i in range(len(self.nextclass_offsets)):
                if self.minibatch_offset < self.nextclass_offsets[i]:
                    self.minibatch_class = i
                    remainder = (self.nextclass_offsets[i] -
                                 self.minibatch_offset)
                    if remainder <= self.minibatch_maxsize:
                        self.last_minibatch <<= True
                        self.minibatch_size = remainder
                        self.info("Last minibatch of class %s served",
                                  CLASS_NAME[i].upper())
                    else:
                        self.last_minibatch <<= False
                        self.minibatch_size = self.minibatch_maxsize
                    break
            else:
                raise error.ErrNotExists(
                    "minibatch_offset is too large: %d", self.minibatch_offset)
        else:
            # Force this minibatch to be the last for the slave
            self.last_minibatch <<= True
        self._update_epoch_ended()

        # Adjust offset according to the calculated step
        assert self.minibatch_size > 0
        self.minibatch_offset += self.minibatch_size
        assert self.minibatch_offset <= len(self.shuffled_indexes)

        # Record and print stats
        self.samples_served += self.minibatch_size
        num, den = divmod(self.samples_served, self.total_samples)
        self.epoch_number = num
        if not self.is_slave:
            now = time.time()
            if now - self._minibatch_serve_timestamp_ >= 10:
                self._minibatch_serve_timestamp_ = now
                self.info("Served %d samples (%d epochs, %.1f%% current)" % (
                    self.samples_served,
                    num, 100.0 * den / self.total_samples))


class IFullBatchLoader(Interface):
    def load_data():
        """Load the data here.
        """


@implementer(ILoader)
class FullBatchLoader(Loader):
    """Loads data entire in memory.

    Attributes:
        original_data: numpy array of original data.
        original_labels: numpy array of original labels
                         (in case of classification).
        original_target: numpy array of original target
                         (in case of MSE).

    Should be overriden in child class:
        load_data()
    """
    def __init__(self, workflow, **kwargs):
        super(FullBatchLoader, self).__init__(workflow, **kwargs)
        self.verify_interface(IFullBatchLoader)

    def init_unpickled(self):
        super(FullBatchLoader, self).init_unpickled()
        self.original_data = False
        self.original_labels = False
        self.original_target = False
        self.shuffled_indexes = False

    def __getstate__(self):
        state = super(FullBatchLoader, self).__getstate__()
        state["original_data"] = None
        state["original_labels"] = None
        state["original_target"] = None
        state["shuffled_indexes"] = None
        return state

    def create_minibatches(self):
        self.minibatch_data.reset()
        sh = [self.minibatch_maxsize]
        sh.extend(self.original_data[0].shape)
        self.minibatch_data.mem = numpy.zeros(
            sh, dtype=opencl_types.dtypes[config.root.common.precision_type])

        self.minibatch_target.reset()
        if not self.original_target is False:
            sh = [self.minibatch_maxsize]
            sh.extend(self.original_target[0].shape)
            self.minibatch_target.mem = numpy.zeros(
                sh,
                dtype=opencl_types.dtypes[config.root.common.precision_type])

        self.minibatch_labels.reset()
        if not self.original_labels is False:
            sh = [self.minibatch_maxsize]
            self.minibatch_labels.mem = numpy.zeros(sh, dtype=numpy.int32)

        self.minibatch_indexes.reset()
        self.minibatch_indexes.mem = numpy.zeros(self.minibatch_maxsize,
                                                 dtype=numpy.int32)

    def fill_minibatch(self):
        minibatch_size = self.minibatch_size

        idxs = self.minibatch_indexes.mem

        assert self.minibatch_offset <= len(self.shuffled_indexes)
        assert (self.minibatch_offset - minibatch_size) >= 0
        idxs[:minibatch_size] = self.shuffled_indexes[
            self.minibatch_offset - minibatch_size:self.minibatch_offset]

        for i, ii in enumerate(idxs[:minibatch_size]):
            self.minibatch_data[i] = self.original_data[int(ii)]

        if not self.original_labels is False:
            for i, ii in enumerate(idxs[:minibatch_size]):
                self.minibatch_labels[i] = self.original_labels[int(ii)]

        if not self.original_target is False:
            for i, ii in enumerate(idxs[:minibatch_size]):
                self.minibatch_target[i] = self.original_target[int(ii)]


@implementer(IFullBatchLoader)
class ImageLoader(FullBatchLoader):
    """Loads images from multiple folders as full batch.

    Attributes:
        test_paths: list of paths with mask for test set,
                    for example: ["/tmp/*.png"].
        validation_paths: list of paths with mask for validation set,
                          for example: ["/tmp/*.png"].
        train_paths: list of paths with mask for train set,
                     for example: ["/tmp/*.png"].
        target_paths: list of paths for target in case of MSE.
        target_by_lbl: dictionary of targets by lbl
                       in case of classification and MSE.

    Should be overriden in child class:
        get_label_from_filename()
        is_valid_filename()
    """
    def __init__(self, workflow, **kwargs):
        test_paths = kwargs.get("test_paths")
        validation_paths = kwargs.get("validation_paths")
        train_paths = kwargs.get("train_paths")
        target_paths = kwargs.get("target_paths")
        grayscale = kwargs.get("grayscale", True)
        kwargs["test_paths"] = test_paths
        kwargs["validation_paths"] = validation_paths
        kwargs["train_paths"] = train_paths
        kwargs["target_paths"] = target_paths
        kwargs["grayscale"] = grayscale
        super(ImageLoader, self).__init__(workflow, **kwargs)
        self.test_paths = test_paths
        self.validation_paths = validation_paths
        self.train_paths = train_paths
        self.target_paths = target_paths
        self.grayscale = grayscale

    def init_unpickled(self):
        super(ImageLoader, self).init_unpickled()
        self.target_by_lbl = {}

    def from_image(self, fnme):
        """Loads data from image and normalizes it.

        Returns:
            numpy array: if there was one image in the file.
            tuple: (a, l) if there were many images in the file
                a - data
                l - labels.
        """
        import scipy.ndimage
        a = scipy.ndimage.imread(fnme, flatten=self.grayscale)
        a = a.astype(numpy.float32)
        if self.normalize:
            formats.normalize(a)
        return a

    def get_label_from_filename(self, filename):
        """Returns label from filename.
        """
        pass

    def is_valid_filename(self, filename):
        return True

    def load_original(self, pathname):
        """Loads data from original files.
        """
        self.info("Loading from %s..." % (pathname))
        files = []
        for basedir, _, filelist in os.walk(pathname):
            for nme in filelist:
                fnme = "%s/%s" % (basedir, nme)
                if self.is_valid_filename(fnme):
                    files.append(fnme)
        files.sort()
        n_files = len(files)
        if not n_files:
            self.warning("No files fetched as %s" % (pathname))
            return [], []

        aa = None
        ll = []

        sz = -1
        this_samples = 0
        next_samples = 0
        for i in range(0, n_files):
            obj = self.from_image(files[i])
            if type(obj) == numpy.ndarray:
                a = obj
                if sz != -1 and a.size != sz:
                    raise error.ErrBadFormat("Found file with different "
                                             "size than first: %s", files[i])
                else:
                    sz = a.size
                lbl = self.get_label_from_filename(files[i])
                if lbl is not None:
                    if type(lbl) != int:
                        raise error.ErrBadFormat(
                            "Found non-integer label "
                            "with type %s for %s" % (str(type(ll)), files[i]))
                    ll.append(lbl)
                if aa is None:
                    sh = [n_files]
                    sh.extend(a.shape)
                    aa = numpy.zeros(sh, dtype=a.dtype)
                next_samples = this_samples + 1
            else:
                a, l = obj[0], obj[1]
                if len(a) != len(l):
                    raise error.ErrBadFormat("from_image() returned different "
                                             "number of samples and labels.")
                if sz != -1 and a[0].size != sz:
                    raise error.ErrBadFormat("Found file with different sample"
                                             " size than first: %s", files[i])
                else:
                    sz = a[0].size
                ll.extend(l)
                if aa is None:
                    sh = [n_files + len(l) - 1]
                    sh.extend(a[0].shape)
                    aa = numpy.zeros(sh, dtype=a[0].dtype)
                next_samples = this_samples + len(l)
            if aa.shape[0] < next_samples:
                aa = numpy.append(aa, a, axis=0)
            aa[this_samples:next_samples] = a
            self.total_samples += next_samples - this_samples
            this_samples = next_samples
        return (aa, ll)

    def load_data(self):
        data = None
        labels = []

        # Loading original data and labels.
        offs = 0
        i = -1
        for t in (self.test_paths, self.validation_paths, self.train_paths):
            i += 1
            if t is None or not len(t):
                continue
            for pathname in t:
                aa, ll = self.load_original(pathname)
                if not len(aa):
                    continue
                if len(ll):
                    if len(ll) != len(aa):
                        raise error.ErrBadFormat(
                            "Number of labels %d differs "
                            "from number of input images %d for %s" %
                            (len(ll), len(aa), pathname))
                    labels.extend(ll)
                elif len(labels):
                    raise error.ErrBadFormat("Not labels found for %s" %
                                             (pathname))
                if data is None:
                    data = aa
                else:
                    data = numpy.append(data, aa, axis=0)
            self.class_samples[i] = len(data) - offs
            offs = len(data)

        if len(labels):
            max_ll = max(labels)
            self.info("Labels are indexed from-to: %d %d" %
                      (min(labels), max_ll))
            self.original_labels = numpy.array(labels, dtype=numpy.int32)

        # Loading target data and labels.
        if self.target_paths is not None:
            n = 0
            for pathname in self.target_paths:
                aa, ll = self.load_original(pathname)
                if len(ll):  # there are labels
                    for i, label in enumerate(ll):
                        self.target_by_lbl[label] = aa[i]
                else:  # assume that target order is the same as data
                    for a in aa:
                        self.target_by_lbl[n] = a
                        n += 1
            if n:
                if n != numpy.sum(self.class_samples):
                    raise error.ErrBadFormat("Target samples count differs "
                                             "from data samples count.")
                self.original_labels = numpy.arange(n, dtype=numpy.int32)

        self.original_data = data

        target = False
        for aa in self.target_by_lbl.values():
            sh = [len(self.original_data)]
            sh.extend(aa.shape)
            target = numpy.zeros(sh, dtype=aa.dtype)
            break
        if target is not False:
            for i, label in enumerate(self.original_labels):
                target[i] = self.target_by_lbl[label]
            self.target_by_lbl.clear()
        self.original_target = target
