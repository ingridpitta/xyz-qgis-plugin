# -*- coding: utf-8 -*-
###############################################################################
#
# Copyright (c) 2019 HERE Europe B.V.
#
# SPDX-License-Identifier: MIT
# License-Filename: LICENSE
#
###############################################################################

from qgis.core import QgsVectorLayer, QgsProject, QgsFeatureRequest

from .layer_utils import is_xyz_supported_layer
class FeatureCache(object):
    def __init__(self):
        self.lst_layer_id = list()
        self.map_added_ids = dict()
        self.map_removed_ids = dict()
    def cb_attr_changed(self, layer_id, attr_map):
        self.add_ids(layer_id, attr_map.keys())
    def cb_feat_added(self, layer_id, lst_feat):
        self.add_ids(layer_id, [ft.id() for ft in lst_feat])
    def add_ids(self, layer_id, lst_feat_id):
        print("add", lst_feat_id)
        if not layer_id in self.map_added_ids:
            self.map_added_ids[layer_id] = set()
        self.map_added_ids[layer_id].union(lst_feat_id)
    def remove_ids(self, layer_id, lst_feat_id):
        print("remove", lst_feat_id)
        if not layer_id in self.map_removed_ids:
            self.map_removed_ids[layer_id] = set()
        self.map_removed_ids[layer_id].union(lst_feat_id)
    def reset(self, layer_id):
        if layer_id in self.map_added_ids:
            self.map_added_ids[layer_id].clear()
        if layer_id in self.map_removed_ids:
            self.map_removed_ids[layer_id].clear()
    def remove_layers(self, lst_layer_id):
        for layer_id in lst_layer_id:
            try: self.lst_layer_id.remove(layer_id)
            except: pass
            self.map_added_ids.pop(layer_id,None)
            self.map_removed_ids.pop(layer_id,None)
    def get_ids(self, layer_id):
        added_ids = self.map_added_ids.get(layer_id, set())
        removed_ids = self.map_removed_ids.get(layer_id, set())
        return list(added_ids), list(removed_ids)
    def get_conn_info(self, layer_id):
        vlayer = QgsProject.instance().mapLayer(layer_id)
        if vlayer is None: return
        return vlayer.customProperty("xyz-hub-conn") 
    def config_connection(self, lst_vlayer):
        
        for vlayer in lst_vlayer:
            if not (isinstance(vlayer, QgsVectorLayer) and is_xyz_supported_layer(vlayer)): continue
            if vlayer.customProperty("xyz-hub-edit") is not None: continue
                
            self.lst_layer_id.append(vlayer.id())
            vlayer.setCustomProperty("xyz-hub-edit", True)
            for signal_name, callback in self._connection_pair():
                signal = getattr(vlayer, signal_name)
                signal.connect(callback)
            
    def unload_connection(self):
        for vlayer in filter(None, map(QgsProject.instance().mapLayer, self.lst_layer_id)):
            vlayer.removeCustomProperty("xyz-hub-edit")
            for signal_name, callback in self._connection_pair():
                signal = getattr(vlayer, signal_name)
                signal.disconnect(callback)
    def _connection_pair(self):
        # vlayer.committedAttributeValuesChanges.connect(self.cb_attr_changed)
        # vlayer.committedGeometriesChanges.connect(self.cb_attr_changed)

        # vlayer.committedFeaturesAdded.connect(self.add_ids)
        # vlayer.committedFeaturesRemoved.connect(self.remove_ids)

        # unsure signal
        # committedAttributesAdded
        # committedAttributesDeleted
        return [
            ("committedAttributeValuesChanges", self.cb_attr_changed),
            ("committedGeometriesChanges", self.cb_attr_changed),
            ("committedFeaturesAdded", self.cb_feat_added),
            ("committedFeaturesRemoved", self.remove_ids),
        ]
