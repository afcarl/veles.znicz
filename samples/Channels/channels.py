#!/usr/bin/python3 -O
"""
Created on April 22, 2014

Model created for logotype of TV channels recognition. Dataset was generated by
VELES. Self-constructing Model. It means that Model can change for any Model
(Convolutional, Fully connected, different parameters) in configuration file.

Copyright (c) 2013 Samsung Electronics Co., Ltd.
"""


# os should be imported first, because of the following workaround
import os
# FIXME(a.kazantsev): numpy.dot works 5 times faster with this option
# in multithreaded mode.
from veles.znicz.image_saver import ImageSaver

os.environ["OPENBLAS_NUM_THREADS"] = "1"

from copy import copy
import glymur
import logging
import numpy
import re
import scipy.misc
import six
import sys
import threading
import time
import traceback
from zope.interface import implementer

from veles.config import root
import veles.error as error
import veles.memory as formats
import veles.image as image
from veles.mutable import Bool
from veles.pickle2 import pickle, best_protocol
import veles.plotting_units as plotting_units
import veles.prng as rnd
import veles.thread_pool as thread_pool
import veles.znicz.all2all as all2all
import veles.znicz.conv as conv
import veles.znicz.decision as decision
import veles.znicz.evaluator as evaluator
import veles.znicz.image_saver as image_saver
import veles.znicz.loader as loader
import veles.znicz.nn_plotting_units as nn_plotting_units
from veles.znicz.nn_units import NNSnapshotter
from veles.znicz.standard_workflow import StandardWorkflowBase
from veles.external.progressbar import ProgressBar


if (sys.version_info[0] + (sys.version_info[1] / 10.0)) < 3.3:
    FileNotFoundError = IOError  # pylint: disable=W0622

root.channels.model = "conv"

root.channels.update({
    "accumulator": {"bars": 30},
    "decision": {"fail_iterations": 1000,
                 "max_epochs": 10000},
    "snapshotter": {"prefix": "channels_%s" % root.channels.model},
    "image_saver": {"out_dirs":
                    [os.path.join(root.common.cache_dir,
                                  "tmp_%s/test" % root.channels.model),
                     os.path.join(root.common.cache_dir,
                                  "tmp_%s/validation" % root.channels.model),
                     os.path.join(root.common.cache_dir,
                                  "tmp_%s/train" % root.channels.model)]},
    "loader": {"cache_file_name": os.path.join(root.common.cache_dir,
                                               "channels_%s.%d.pickle" %
                                               (root.channels.model,
                                                sys.version_info[0])),
               "grayscale": False,
               "minibatch_size": 81,
               "n_threads": 32,
               "channels_dir": "",
               "rect": (264, 129),
               "validation_ratio": 0.15},
    "weights_plotter": {"limit": 64},
    "export": False,
    "find_negative": 0,
    "learning_rate": 0.00001,
    "weights_decay": 0.004,
    "layers": [{"type": "conv", "n_kernels": 32,
                "kx": 5, "ky": 5, "padding": (2, 2, 2, 2)},
               {"type": "max_pooling", "kx": 3, "ky": 3, "sliding": (2, 2)},
               {"type": "conv", "n_kernels": 32,
                "kx": 5, "ky": 5, "padding": (2, 2, 2, 2)},
               {"type": "avg_pooling", "kx": 3, "ky": 3, "sliding": (2, 2)},
               {"type": "conv", "n_kernels": 64,
                "kx": 5, "ky": 5, "padding": (2, 2, 2, 2)},
               {"type": "avg_pooling", "kx": 3, "ky": 3, "sliding": (2, 2)},
               {"type": "softmax", "output_shape": 11}],
    "snapshot": ""})


@implementer(loader.IFullBatchLoader)
class ChannelsLoader(loader.FullBatchLoader):
    """Loads channels.
    """
    def __init__(self, workflow, **kwargs):
        super(ChannelsLoader, self).__init__(workflow, **kwargs)
        self.channels_dir = kwargs.get("channels_dir", "")
        self.layers = kwargs.get("layers", [54, 10])
        self.rect = kwargs.get("rect", (264, 129))
        self.grayscale = kwargs.get("grayscale", False)
        self.cache_file_name = kwargs.get("cache_file_name", "")
        self.find_negative = kwargs.get("find_negative", 0)
        self.n_threads = kwargs.get("n_threads", 32)
        # : Top-level configuration from channels_dir/conf.py
        self.top_conf_ = None
        # : Configuration from channels_dir/subdirectory/conf.py
        self.subdir_conf_ = {}
        self.w_neg = None  # workflow for finding the negative dataset
        self.channel_map = None
        self.pos = {}
        self.sz = {}
        self.file_map = {}  # sample index to its file name map
        self.attributes_for_cached_data = [
            "channels_dir", "rect", "channel_map", "pos", "sz",
            "class_lengths", "grayscale", "file_map", "cache_file_name"]
        self.exports = ["rect", "pos", "sz"]
        self.do_swap_axis = False

    def from_jp2(self, fnme):
        try:
            j2 = glymur.Jp2k(fnme)
        except:
            self.error("glymur.Jp2k() failed for %s" % (fnme))
            raise
        a2 = j2.read()
        if j2.box[2].box[1].colorspace == 16:  # RGB
            if self.grayscale:
                # Get Y component from RGB
                a = numpy.empty([a2.shape[0], a2.shape[1], 1],
                                dtype=numpy.uint8)
                a[:, :, 0:1] = numpy.clip(
                    0.299 * a2[:, :, 0:1] +
                    0.587 * a2[:, :, 1:2] +
                    0.114 * a2[:, :, 2:3], 0, 255)
                a = formats.reshape(a, [a2.shape[0], a2.shape[1]])
            else:
                # Convert to YUV
                # Y = 0.299 * R + 0.587 * G + 0.114 * B;
                # U = -0.14713 * R - 0.28886 * G + 0.436 * B + 128;
                # V = 0.615 * R - 0.51499 * G - 0.10001 * B + 128;
                # and transform to different planes
                a = numpy.empty([3, a2.shape[0], a2.shape[1]],
                                dtype=numpy.uint8)
                a[0:1, :, :].reshape(
                    a2.shape[0], a2.shape[1], 1)[:, :, 0:1] = numpy.clip(
                    0.299 * a2[:, :, 0:1] +
                    0.587 * a2[:, :, 1:2] +
                    0.114 * a2[:, :, 2:3], 0, 255)
                a[1:2, :, :].reshape(
                    a2.shape[0], a2.shape[1], 1)[:, :, 0:1] = numpy.clip(
                    (-0.14713) * a2[:, :, 0:1] +
                    (-0.28886) * a2[:, :, 1:2] +
                    0.436 * a2[:, :, 2:3] + 128, 0, 255)
                a[2:3, :, :].reshape(
                    a2.shape[0], a2.shape[1], 1)[:, :, 0:1] = numpy.clip(
                    0.615 * a2[:, :, 0:1] +
                    (-0.51499) * a2[:, :, 1:2] +
                    (-0.10001) * a2[:, :, 2:3] + 128, 0, 255)
        elif j2.box[2].box[1].colorspace == 18:  # YUV
            if self.grayscale:
                a = numpy.empty([a2.shape[0], a2.shape[1], 1],
                                dtype=numpy.uint8)
                a[:, :, 0:1] = a2[:, :, 0:1]
                a = formats.reshape(a, [a2.shape[0], a2.shape[1]])
            else:
                # transform to different yuv planes
                a = numpy.empty([3, a2.shape[0], a2.shape[1]],
                                dtype=numpy.uint8)
                a[0:1, :, :].reshape(
                    a2.shape[0], a2.shape[1], 1)[:, :, 0:1] = a2[:, :, 0:1]
                a[1:2, :, :].reshape(
                    a2.shape[0], a2.shape[1], 1)[:, :, 0:1] = a2[:, :, 1:2]
                a[2:3, :, :].reshape(
                    a2.shape[0], a2.shape[1], 1)[:, :, 0:1] = a2[:, :, 2:3]
        else:
            raise error.BadFormatError("Unknown colorspace in %s" % (fnme))
        return a

    def sample_rect(self, a, pos, sz):
        if self.grayscale:
            aa = numpy.empty([self.rect[1], self.rect[0]], dtype=numpy.float32)
            x = a
            left = int(numpy.round(pos[0] * x.shape[1]))
            top = int(numpy.round(pos[1] * x.shape[0]))
            width = int(numpy.round(sz[0] * x.shape[1]))
            height = int(numpy.round(sz[1] * x.shape[0]))
            x = x[top:top + height, left:left + width].ravel().copy().\
                reshape((height, width), order="C")
            x = image.resize(x, self.rect[0], self.rect[1])
            aa[:] = x[:]
        else:
            aa = numpy.empty([3, self.rect[1], self.rect[0]],
                             dtype=numpy.float32)
            # Loop by color planes.
            for j in range(0, a.shape[0]):
                x = a[j]
                left = int(numpy.round(pos[0] * x.shape[1]))
                top = int(numpy.round(pos[1] * x.shape[0]))
                width = int(numpy.round(sz[0] * x.shape[1]))
                height = int(numpy.round(sz[1] * x.shape[0]))
                x = x[top:top + height, left:left + width].ravel().copy().\
                    reshape((height, width), order="C")
                x = image.resize(x, self.rect[0], self.rect[1])
                aa[j] = x

        if self.grayscale:
            formats.normalize(aa)
        else:
            # Normalize Y and UV planes separately.
            formats.normalize(aa[0])
            formats.normalize(aa[1:])

        return aa

    def append_sample(self, sample, lbl, fnme, n_negative, data_lock):
        data_lock.acquire()
        self._original_data.append(sample)
        self._original_labels.append(lbl)
        ii = len(self._original_data) - 1
        self.file_map[ii] = fnme
        if n_negative is not None:
            n_negative[0] += 1
        data_lock.release()
        return ii

    def from_jp2_async(self, fnme, pos, sz, data_lock, stat_lock,
                       i_sample, lbl, n_files, total_files,
                       n_negative, rand, progress):
        """Loads, crops and normalizes image in the parallel thread.
        """
        a = self.from_jp2(fnme)

        sample = self.sample_rect(a, pos, sz)
        if self.do_swap_axis is True:
            sample = numpy.swapaxes(sample, 0, 1)
            sample = numpy.swapaxes(sample, 1, 2)

        self.append_sample(sample, lbl, fnme, None, data_lock)

        # Collect negative dataset from positive samples only
        if lbl and self.w_neg is not None and self.find_negative > 0:
            # Sample pictures at random positions
            samples = numpy.zeros([self.find_negative, sample.size],
                                  dtype=self.w_neg[0][0].dtype)
            for i in range(self.find_negative):
                t = rand.randint(2)
                if t == 0:
                    # Sample vertical line
                    p = [pos[0] + (1 if pos[0] < 0.5 else -1) * sz[0],
                         rand.rand() * (1.0 - sz[1])]
                elif t == 1:
                    # Sample horizontal line
                    p = [rand.rand() * (1.0 - sz[0]),
                         pos[1] + (1 if pos[1] < 0.5 else -1) * sz[1]]
                else:
                    continue
                samples[i][:] = self.sample_rect(a, p, sz).ravel()[:]
            ll = self.get_labels_from_samples(samples)
            for i, l in enumerate(ll):
                if l == 0:
                    continue
                # negative found
                s = samples[i].reshape(sample.shape)
                ii = self.append_sample(s, 0, fnme, n_negative, data_lock)
                dirnme = "%s/found_negative_images" % (root.common.cache_dir)
                try:
                    os.mkdir(dirnme)
                except OSError:
                    pass
                fnme = "%s/0_as_%d.%d.png" % (dirnme, l, ii)
                scipy.misc.imsave(fnme, ImageSaver.as_image(s))

        with stat_lock:
            n_files[0] += 1
            progress.inc()

    def get_labels_from_samples(self, samples):
        weights = self.w_neg[0]
        bias = self.w_neg[1]
        n = len(weights)
        a = samples
        for i in range(n):
            a = numpy.dot(a, weights[i].transpose())
            a += bias[i]
            if i < n - 1:
                a *= 0.6666
                numpy.tanh(a, a)
                a *= 1.7159
        return a.argmax(axis=1)

    def get_label(self, dirnme):
        lbl = self.channel_map[dirnme].get("lbl")
        if lbl is None:
            lbl = int(dirnme)
        return lbl

    def _data_labels_to_vector(self):
        self.original_data.mem = numpy.empty(
            [len(self._original_data)] + list(self._original_data[0].shape),
            dtype=numpy.float32)
        i = 0
        while len(self._original_data):
            self.original_data.mem[i] = self._original_data.pop(0)
            i += 1
        del self._original_data

        self.original_labels.mem = numpy.empty(
            len(self._original_labels), dtype=numpy.int32)
        i = 0
        while len(self._original_labels):
            self.original_labels.mem[i] = self._original_labels.pop(0)
            i += 1
        del self._original_labels

    def load_data(self):
        if (self.original_data.mem is not None and
                self.original_labels is not None):
            self.info("Data and Labels already initialized")
            return

        self.original_data.reset()
        self.original_labels.reset()

        cached_data_fnme = (
            self.cache_file_name or os.path.join(
                root.common.cache_dir,
                "%s_%s.%d.pickle" %
                (os.path.basename(__file__), self.__class__.__name__,
                 best_protocol)))
        self.info("Will try to load previously cached data from " +
                  cached_data_fnme)
        save_to_cache = True
        self.do_swap_axis = False
        for i in range(0, len(self.layers)):
            if self.layers[i].get("n_kernels") is not None:
                self.do_swap_axis = True
        try:
            with open(cached_data_fnme, "rb") as fin:
                cache = pickle.load(fin)
                self._original_data = [pickle.load(fin) for _
                                       in range(len(cache["original_labels"]))]
            obj = cache["obj"]
            if obj["channels_dir"] != self.channels_dir:
                save_to_cache = False
                self.info("different dir found in cached data: %s" % (
                    obj["channels_dir"]))
                fin.close()
                raise FileNotFoundError()
            for k, v in obj.items():
                if type(v) == list:
                    o = self.__dict__[k]
                    if o is None:
                        o = []
                        self.__dict__[k] = o
                    del o[:]
                    o.extend(v)
                elif type(v) == dict:
                    o = self.__dict__[k]
                    if o is None:
                        o = {}
                        self.__dict__[k] = o
                    o.update(v)
                else:
                    self.__dict__[k] = v

            for k in self.pos.keys():
                self.info("%s: pos=(%.6f, %.6f) sz=(%.6f, %.6f)" % (
                    k, self.pos[k][0], self.pos[k][1],
                    self.sz[k][0], self.sz[k][1]))
            self.info("rect: (%d, %d)" % (self.rect[0], self.rect[1]))

            self.shuffled_indices.mem = cache["shuffled_indices"]
            self._original_labels = list(cache["original_labels"])
            # Get raw array from file
            store_negative = self.w_neg is not None and self.find_negative > 0
            self.file_map = cache["file_map"]
            self.prng.state = cache["prng"]
            self.info("Succeeded, class_lengths=[%s]" % (
                ", ".join(str(x) for x in self.class_lengths)))
            if not store_negative:
                self._data_labels_to_vector()
                return
            self.info("Will search for a negative set at most %d "
                      "samples per image" % (self.find_negative))
            # Saving the old negative set
            self.info("Extracting the old negative set")
            n = len(self._original_data)
            self._original_labels = [0] * n
            self.shuffled_indices.reset()
            self.info("Done (%d extracted)" % n)
        except FileNotFoundError:
            self.info("Failed")
            self._original_labels = []
            self._original_data = []
            self.shuffled_indices.reset()
            self.file_map.clear()

        self.info("Will load data from original jp2 files")

        # Read top-level configuration
        try:
            with open(os.path.join(self.channels_dir, "conf.py"), "r") as fin:
                s = fin.read()
            self.top_conf_ = {}
            six.exec_(s, self.top_conf_, self.top_conf_)
        except:
            self.error("Error while executing %s/conf.py" % (
                self.channels_dir))
            raise

        # Read subdirectories configurations
        self.subdir_conf_.clear()
        for subdir in self.top_conf_["dirs_to_scan"]:
            try:
                with open("%s/%s/conf.py" % (self.channels_dir, subdir), "r") \
                        as fin:
                    s = fin.read()
                self.subdir_conf_[subdir] = {}
                six.exec_(s, self.subdir_conf_[subdir],
                          self.subdir_conf_[subdir])
            except:
                self.error("Error while executing %s/%s/conf.py" % (
                    self.channels_dir, subdir))
                raise

        # Parse configs
        self.channel_map = self.top_conf_["channel_map"]
        pos = {}
        rpos = {}
        sz = {}
        for subdir, subdir_conf in self.subdir_conf_.items():
            frame = subdir_conf["frame"]
            if subdir not in pos.keys():
                pos[subdir] = copy(frame)  # bottom-right corner
                rpos[subdir] = [0, 0]
            for pos_size in subdir_conf["channel_map"].values():
                pos[subdir][0] = min(pos[subdir][0], pos_size["pos"][0])
                pos[subdir][1] = min(pos[subdir][1], pos_size["pos"][1])
                rpos[subdir][0] = max(rpos[subdir][0],
                                      pos_size["pos"][0] + pos_size["size"][0])
                rpos[subdir][1] = max(rpos[subdir][1],
                                      pos_size["pos"][1] + pos_size["size"][1])
            # Convert to relative values
            pos[subdir][0] /= frame[0]
            pos[subdir][1] /= frame[1]
            rpos[subdir][0] /= frame[0]
            rpos[subdir][1] /= frame[1]
            sz[subdir] = [rpos[subdir][0] - pos[subdir][0],
                          rpos[subdir][1] - pos[subdir][1]]

        self.info("Found rectangles:")
        for k in pos.keys():
            self.info("%s: pos=(%.6f, %.6f) sz=(%.6f, %.6f)" % (
                k, pos[k][0], pos[k][1], sz[k][0], sz[k][1]))

        self.info("Adjusted rectangles:")
        for k in pos.keys():
            # sz[k][0] *= 1.01
            # sz[k][1] *= 1.01
            pos[k][0] += (rpos[k][0] - pos[k][0] - sz[k][0]) * 0.5
            pos[k][1] += (rpos[k][1] - pos[k][1] - sz[k][1]) * 0.5
            pos[k][0] = min(pos[k][0], 1.0 - sz[k][0])
            pos[k][1] = min(pos[k][1], 1.0 - sz[k][1])
            pos[k][0] = max(pos[k][0], 0.0)
            pos[k][1] = max(pos[k][1], 0.0)
            self.info("%s: pos=(%.6f, %.6f) sz=(%.6f, %.6f)" % (
                k, pos[k][0], pos[k][1], sz[k][0], sz[k][1]))

        self.pos.clear()
        self.pos.update(pos)
        self.sz.clear()
        self.sz.update(sz)

        max_lbl = 0
        files = {}
        total_files = 0
        baddir = re.compile("bad", re.IGNORECASE)
        jp2 = re.compile("\.jp2$", re.IGNORECASE)
        for subdir, subdir_conf in self.subdir_conf_.items():
            for dirnme in subdir_conf["channel_map"].keys():
                max_lbl = max(max_lbl, self.get_label(dirnme))
                relpath = "%s/%s" % (subdir, dirnme)
                found_files = []
                fordel = []
                for basedir, dirlist, filelist in os.walk(
                        "%s/%s" % (self.channels_dir, relpath)):
                    for i, nme in enumerate(dirlist):
                        if baddir.search(nme) is not None:
                            fordel.append(i)
                    while len(fordel) > 0:
                        dirlist.pop(fordel.pop())
                    for nme in filelist:
                        if jp2.search(nme) is not None:
                            found_files.append("%s/%s" % (basedir, nme))
                found_files.sort()
                files[relpath] = found_files
                total_files += len(found_files)
        self.info("Found %d files" % (total_files))

        # Read samples in parallel
        rand = rnd.get()
        rand.seed(numpy.fromfile("/dev/urandom", dtype=numpy.int32,
                                 count=1024))
        # FIXME(a.kazantsev): numpy.dot is thread-safe with this value
        # on ubuntu 13.10 (due to the static number of buffers in libopenblas)
        if not root.common.unit_test:
            n_threads = self.n_threads
            pool = thread_pool.ThreadPool(minthreads=1, maxthreads=n_threads,
                                          queue_size=n_threads)
        data_lock = threading.Lock()
        stat_lock = threading.Lock()
        n_files = [0]
        n_negative = [0]
        i_sample = 0
        progress = ProgressBar(maxval=total_files, term_width=27)
        progress.start()
        for subdir in sorted(self.subdir_conf_.keys()):
            subdir_conf = self.subdir_conf_[subdir]
            for dirnme in sorted(subdir_conf["channel_map"].keys()):
                relpath = "%s/%s" % (subdir, dirnme)
                self.info("Will load from %s" % (relpath))
                lbl = self.get_label(dirnme)
                for fnme in files[relpath]:
                    if root.common.unit_test:
                        self.from_jp2_async(
                            fnme, pos[subdir], sz[subdir],
                            data_lock, stat_lock,
                            0 + i_sample, 0 + lbl, n_files,
                            total_files, n_negative, rand, progress)
                    else:
                        pool.callInThread(
                            self.from_jp2_async,
                            fnme, pos[subdir], sz[subdir],
                            data_lock, stat_lock,
                            0 + i_sample, 0 + lbl, n_files,
                            total_files, n_negative, rand, progress)
                    i_sample += 1
        if not root.common.unit_test:
            pool.shutdown(execute_remaining=True)
        progress.finish()

        if (len(self._original_data) != len(self._original_labels) or
                len(self.file_map) != len(self._original_labels)):
            raise Exception("Logic error")

        if self.w_neg is not None and self.find_negative > 0:
            n_positive = numpy.count_nonzero(self._original_labels)
            self.info("Found %d negative samples (%.2f%%)" % (
                n_negative[0], 100.0 * n_negative[0] / n_positive))

        self.info("Loaded %d samples with resize and %d without" % (
            image.resize_count, image.asitis_count))

        self.class_lengths[0] = 0
        self.class_lengths[1] = 0
        self.class_lengths[2] = len(self._original_data)

        # Randomly generate validation set from train.
        self.info("Will extract validation set from train")
        self._data_labels_to_vector()
        self.extract_validation_from_train(rnd.get(2))

        # Saving all the samples
        self.info("Dumping all the samples to %s" % (root.common.cache_dir))
        for i in self.shuffled_indices.mem:
            l = self.original_labels[i]
            dirnme = "%s/%s" % (root.common.cache_dir, root.channels.model)
            try:
                os.mkdir(dirnme)
            except OSError:
                pass
            dirnme = "%s/%03d" % (dirnme, l)
            try:
                os.mkdir(dirnme)
            except OSError:
                pass
            fnme = "%s/%d.png" % (dirnme, i)
            scipy.misc.imsave(fnme, ImageSaver.as_image(self.original_data[i]))
        self.info("Done")

        self.info("class_lengths=[%s]" % (
            ", ".join(str(x) for x in self.class_lengths)))
        if not save_to_cache:
            return
        self.info("Saving loaded data for later faster load to "
                  "%s" % cached_data_fnme)
        with open(cached_data_fnme, "wb") as fout:
            obj = {}
            for name in self.attributes_for_cached_data:
                obj[name] = self.__dict__[name]
            cache = {"obj": obj, "shuffled_indices": self.shuffled_indices.mem,
                     "original_labels": self.original_labels.mem,
                     "prng": self.prng.state,
                     "file_map": self.file_map}
            pickle.dump(cache, fout, protocol=best_protocol)
            for item in self.original_data.mem:
                pickle.dump(item, fout)
        #fout.close()
        self.info("Done")


class ChannelsWorkflow(StandardWorkflowBase):
    """Workflow.
    """
    def __init__(self, workflow, **kwargs):
        layers = kwargs.get("layers")
        device = kwargs.get("device")
        kwargs["layers"] = layers
        kwargs["device"] = device
        kwargs["name"] = kwargs.get("name", "channels")
        super(ChannelsWorkflow, self).__init__(workflow, **kwargs)

        # self.saver = None

        self.repeater.link_from(self.start_point)

        self.loader = ChannelsLoader(
            self, cache_file_name=root.channels.loader.cache_file_name,
            find_negative=root.channels.find_negative,
            grayscale=root.channels.loader.grayscale,
            n_threads=root.channels.loader.n_threads,
            channels_dir=root.channels.loader.channels_dir,
            rect=root.channels.loader.rect,
            validation_ratio=root.channels.loader.validation_ratio,
            layers=root.channels.layers)
        self.loader.link_from(self.repeater)

        # Add fwds units
        self.parse_forwards_from_config(self.loader,
                                        ("input", "minibatch_data"))

        # Add Image Saver unit
        self.image_saver = image_saver.ImageSaver(
            self, out_dirs=root.channels.image_saver.out_dirs)
        self.image_saver.link_from(self.forwards[-1])
        self.image_saver.link_attrs(self.forwards[-1], "output", "max_idx")
        self.image_saver.link_attrs(
            self.loader,
            ("input", "minibatch_data"),
            ("indexes", "minibatch_indices"),
            ("labels", "minibatch_labels"),
            "minibatch_class", "minibatch_size")

        # Add evaluator for single minibatch
        self.evaluator = evaluator.EvaluatorSoftmax(self, device=device)
        self.evaluator.link_from(self.image_saver)
        self.evaluator.link_attrs(self.forwards[-1], "output", "max_idx")
        self.evaluator.link_attrs(self.loader,
                                  ("batch_size", "minibatch_size"),
                                  ("labels", "minibatch_labels"),
                                  ("max_samples_per_epoch", "total_samples"))

        # Add decision unit
        self.decision = decision.DecisionGD(
            self, fail_iterations=root.channels.decision.fail_iterations,
            max_epochs=root.channels.decision.max_epochs)
        self.decision.link_from(self.evaluator)
        self.decision.link_attrs(self.loader,
                                 "minibatch_class", "minibatch_size",
                                 "last_minibatch", "class_lengths",
                                 "epoch_ended", "epoch_number")
        self.decision.link_attrs(
            self.evaluator,
            ("minibatch_n_err", "n_err"),
            ("minibatch_confusion_matrix", "confusion_matrix"))

        self.snapshotter = NNSnapshotter(
            self, prefix=root.channels.snapshotter.prefix,
            directory=root.common.snapshot_dir)
        self.snapshotter.link_from(self.decision)
        self.snapshotter.link_attrs(self.decision,
                                    ("suffix", "snapshot_suffix"))
        self.snapshotter.gate_skip = \
            (~self.decision.epoch_ended | ~self.decision.improved)

        self.image_saver.gate_skip = ~self.decision.improved
        self.image_saver.link_attrs(self.snapshotter,
                                    ("this_save_time", "time"))

        self.create_gd_units_by_config(self.snapshotter)

        # Error plotter
        self.plt = []
        styles = ["r-", "b-", "k-"]
        for i in range(1, 3):
            self.plt.append(plotting_units.AccumulatingPlotter(
                self, name="num errors", plot_style=styles[i]))
            self.plt[-1].link_attrs(self.decision, ("input", "epoch_n_err_pt"))
            self.plt[-1].input_field = i
            self.plt[-1].link_from(self.decision
                                   if len(self.plt) == 1 else self.plt[-2])
            self.plt[-1].gate_block = (~self.decision.epoch_ended
                                       if len(self.plt) == 1 else Bool(False))
        self.plt[0].clear_plot = True
        self.plt[-1].redraw_plot = True

        # Confusion matrix plotter
        self.plt_mx = []
        for i in range(1, len(self.decision.confusion_matrixes)):
            self.plt_mx.append(plotting_units.MatrixPlotter(
                self, name=(("Test", "Validation", "Train")[i] + " matrix")))
            self.plt_mx[-1].link_attrs(self.decision,
                                       ("input", "confusion_matrixes"))
            self.plt_mx[-1].input_field = i
            self.plt_mx[-1].link_from(self.plt[-1])
            self.plt_mx[-1].gate_block = ~self.decision.epoch_ended

        # Weights plotter
        self.plt_mx = []
        prev_channels = 3
        for i in range(0, len(layers)):
            if (not isinstance(self.forwards[i], conv.Conv) and
                    not isinstance(self.forwards[i], all2all.All2All)):
                continue
            plt_mx = nn_plotting_units.Weights2D(
                self, name="%s %s" % (i + 1, layers[i]["type"]),
                limit=root.channels.weights_plotter.limit)
            self.plt_mx.append(plt_mx)
            self.plt_mx[-1].link_attrs(self.forwards[i], ("input", "weights"))
            if isinstance(self.forwards[i], conv.Conv):
                self.plt_mx[-1].get_shape_from = (
                    [self.forwards[i].kx, self.forwards[i].ky, prev_channels])
                prev_channels = self.forwards[i].n_kernels
            if (layers[i].get("output_shape") is not None and
                    layers[i]["type"] != "softmax"):
                self.plt_mx[-1].link_attrs(self.forwards[i],
                                           ("get_shape_from", "input"))
            # TO_DO(lyubov.p):
            # Fix fwds input shape from (minibatch, channels, y, x) to
            # (minibatch, y, x, channels). Now fwds input shape incorrect
            # for weights plotter
            self.plt_mx[-1].link_from(self.decision)
            self.plt_mx[-1].gate_block = ~self.decision.epoch_ended

        # MultiHistogram plotter
        self.plt_multi_hist = []
        for i in range(0, len(layers)):
            multi_hist = plotting_units.MultiHistogram(
                self, name="Histogram %s %s" % (i + 1, layers[i]["type"]))
            self.plt_multi_hist.append(multi_hist)
            if layers[i].get("n_kernels") is not None:
                self.plt_multi_hist[i].link_from(self.decision)
                self.plt_multi_hist[i].hist_number = layers[i]["n_kernels"]
                self.plt_multi_hist[i].link_attrs(self.forwards[i],
                                                  ("input", "weights"))
                end_epoch = ~self.decision.epoch_ended
                self.plt_multi_hist[i].gate_block = end_epoch
            if layers[i].get("output_shape") is not None:
                self.plt_multi_hist[i].link_from(self.decision)
                self.plt_multi_hist[i].hist_number = layers[i]["output_shape"]
                self.plt_multi_hist[i].link_attrs(self.forwards[i],
                                                  ("input", "weights"))
                self.plt_multi_hist[i].gate_block = ~self.decision.epoch_ended

        # repeater and gate block
        self.repeater.link_from(self.gds[0])
        self.end_point.link_from(self.snapshotter)
        self.end_point.gate_block = ~self.decision.complete
        self.loader.gate_block = self.decision.complete
        self.gds[-1].gate_block = self.decision.complete

    def initialize(self, learning_rate, weights_decay, minibatch_size, w_neg,
                   device, **kwargs):
        super(ChannelsWorkflow, self).initialize(
            learning_rate=learning_rate, weights_decay=weights_decay,
            minibatch_size=minibatch_size, w_neg=w_neg, device=device)


def run(load, main):
    w_neg = None
    try:
        w, _ = load(ChannelsWorkflow, layers=root.channels.layers)
        if root.channels.export:
            tm = time.localtime()
            s = "%d.%02d.%02d_%02d.%02d.%02d" % (
                tm.tm_year, tm.tm_mon, tm.tm_mday,
                tm.tm_hour, tm.tm_min, tm.tm_sec)
            fnme = os.path.join(root.common.snapshot_dir,
                                "channels_workflow_%s" % s)
            try:
                w.export(fnme)
                logging.info("Exported successfully to %s.tar.gz" % (fnme))
            except:
                a, b, c = sys.exc_info()
                traceback.print_exception(a, b, c)
                logging.error("Error while exporting.")
            return
        if root.channels.find_negative > 0:
            if type(w) != tuple or len(w) != 2:
                logging.error(
                    "Snapshot with weights and biases only "
                    "should be provided when find_negative is supplied. "
                    "Will now exit.")
                return
            w_neg = w
            raise IOError()
    except IOError:
        if root.channels.export:
            logging.error("Valid snapshot should be provided if "
                          "export is True. Will now exit.")
            return
        if root.channels.find_negative > 0 and w_neg is None:
            logging.error("Valid snapshot should be provided if "
                          "find_negative supplied. Will now exit.")
            return
    fnme = (os.path.join(root.common.cache_dir,
                         root.channels.snapshotter.prefix) + ".txt")
    logging.info("Dumping file map to %s" % (fnme))
    fout = open(fnme, "w")
    if w is not None:
        file_map = w.loader.file_map
        for i in sorted(file_map.keys()):
            fout.write("%d\t%s\n" % (i, file_map[i]))
        fout.close()
    logging.info("Done")
    logging.info("Will execute workflow now")
    main(learning_rate=root.channels.learning_rate,
         weights_decay=root.channels.weights_decay,
         minibatch_size=root.channels.loader.minibatch_size,
         w_neg=w_neg)