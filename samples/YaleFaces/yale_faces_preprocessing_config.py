#!/usr/bin/python3 -O
"""
Created on Nov 13, 2014

Configuration file for yale-faces (Self-constructing Model).
Model - fully connected neural network.

Copyright (c) 2013 Samsung Electronics Co., Ltd.
"""


import os
from veles.config import root


root.yalefaces.update({
    "loader": {"minibatch_size": 40, "force_cpu": False,
               "validation_ratio": 0.15,
               "filename_types": ["x-portable-graymap"],
               "ignored_files": [".*Ambient.*"],
               "shuffle_limit": 100000000000000,
               "add_sobel": False,
               "normalization_type": (-1, 1),
               "mirror": False,
               "color_space": "GRAY",
               "background_color": (0,),
               "train_paths":
               [os.path.join(root.common.test_dataset_root, "CroppedYale")]}})
