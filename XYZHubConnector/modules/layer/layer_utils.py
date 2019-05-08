# -*- coding: utf-8 -*-
###############################################################################
#
# Copyright (c) 2019 HERE Europe B.V.
#
# SPDX-License-Identifier: MIT
# License-Filename: LICENSE
#
###############################################################################

from ..controller import make_qt_args
from . import parser
def get_feat_iter(vlayer):
    # assert isinstance(vlayer, QgsVectorLayer)
    return vlayer.getFeatures(), vlayer
def get_feat_upload_from_iter_args(feat_iter, vlayer):
    a = get_feat_upload_from_iter(feat_iter, vlayer)
    return make_qt_args(*a)
def get_feat_upload_from_iter(feat_iter, vlayer):
    added_feat = parser.feature_to_xyz_json(list(feat_iter), vlayer, is_new=False) 
    obj = parser.make_lst_feature_collection(added_feat)
    removed_feat = list()
    return obj, removed_feat
def is_xyz_supported_layer(vlayer):
    meta = vlayer.customProperty("xyz-hub")
    flag = meta is not None
    return flag