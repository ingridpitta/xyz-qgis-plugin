# -*- coding: utf-8 -*-
###############################################################################
#
# Copyright (c) 2019 HERE Europe B.V.
#
# SPDX-License-Identifier: MIT
# License-Filename: LICENSE
#
###############################################################################

import copy
import json

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QThreadPool

from ..controller import (AsyncFun, BasicSignal, ChainController,
                          ChainInterrupt, LoopController, NetworkFun,
                          WorkerFun, make_qt_args, parse_exception_obj,
                          parse_qt_args)

from ..layer import XYZLayer, bbox_utils, layer_utils, parser, queue, render
from ..network import net_handler
from .loop_loader import BaseLoader, BaseLoop, ParallelFun

########################
# Load
########################

class EmptyXYZSpaceError(Exception):
    pass
class InvalidXYZLayerError(Exception):
    pass
class ManualInterrupt(Exception):
    pass
    
class LoadLayerController(BaseLoader):
    """ Load XYZ space into several qgis layer separated by Geometry type.
    If space is empty, no layer shall be created.
    Stateful controller
    """
    def __init__(self, network, n_parallel=1):
        super(LoadLayerController, self).__init__()
        self.pool = QThreadPool() # .globalInstance() will crash afterward
        self.n_parallel = 1
        self.status = self.LOADING
        self._config(network)
    def post_render(self):
        for v in self.layer.map_vlayer.values():
            v.triggerRepaint()
    def start(self, conn_info, meta, **kw):
        tags = kw.get("tags","")
        self.layer = XYZLayer(conn_info, meta, tags=tags)
        self.kw = kw
        self.max_feat = kw.get("max_feat", None)
        self.fixed_params = dict( (k,kw[k]) for k in ["tags"] if k in kw)

        self.signal.finished.connect(self.post_render)

        # super(BaseLoader,self): super of BaseLoader 
        super(LoadLayerController, self).start( **kw)
    def start_args(self, args):
        a, kw = parse_qt_args(args)
        self.start( *a, **kw)
    def reload(self, **kw):
        if self.status != self.FINISHED: return
        self.reset(**kw)

    def reset(self, **kw):
        BaseLoader.reset(self, **kw)
        params = dict(
            limit=kw.get("limit") or 1,
            handle=kw.get("handle", 0),
        )
        self.params_queue = queue.ParamsQueue_deque_smart(params, buffer_size=1)

    def _config(self, network):
        self.config_fun([
            NetworkFun( network.load_features_iterate), 
            WorkerFun( net_handler.on_received, self.pool),
            AsyncFun( self._process_render), 
            WorkerFun( render.parse_feature, self.pool),
            AsyncFun( self._dispatch_render), 
            ParallelFun( self._render_single), 
        ])

    def _check_status(self):
        assert self.status != self.FINISHED
        # print(self.status)
        if self.status == self.STOPPED: 
            self.signal.error.emit(ManualInterrupt())
            return False
        elif self.status == self.ALL_FEAT:
            if not self.params_queue.has_retry():
                self._try_finish()
                return False
        elif self.status == self.MAX_FEAT:
            self._try_finish()
            return False
        feat_cnt = self.get_feat_cnt()
        if self.max_feat is not None and feat_cnt >= self.max_feat:
            self.status = self.MAX_FEAT
            self._try_finish()
            return False
        return True
    def _run(self):
        conn_info = self.layer.conn_info
            
        # if not self.params_queue.has_next():
        #     self.params_queue.gen_params()
        params = self.params_queue.get_params()
        
        LoopController.start(self, conn_info, **params, **self.fixed_params)
    def _emit_finish(self):
        super()._emit_finish()
        token, space_id = self.layer.conn_info.get_xyz_space()
        name = self.layer.get_name()
        msg = "Layer: %s. Token: %s"%(name, token)
        self.signal.results.emit( make_qt_args(msg))
    ##### custom fun

    def _process_render(self,txt,*a,**kw):
        # check if all feat fetched
        obj = json.loads(txt)
        feat_cnt = len(obj["features"])
        total_cnt = self.get_feat_cnt()
        if feat_cnt + total_cnt == 0:
            raise EmptyXYZSpaceError()
        # limit = kw["limit"]
        # if feat_cnt == 0 or feat_cnt < limit:
        if "handle" in obj:
            handle = int(obj["handle"])
            if not self.params_queue.has_next():
                self.params_queue.gen_params(handle=handle)
        else:
            if self.status == self.LOADING:
                self.status = self.ALL_FEAT
        map_fields = self.layer.get_map_fields()
        return make_qt_args(txt, map_fields)
    
    # non-threaded
    def _render(self, *parsed_feat):
        map_feat, map_fields = parsed_feat
        for geom in map_feat.keys():

            if not self.layer.is_valid( geom):
                vlayer=self.layer.show_ext_layer(geom)
            else:
                vlayer=self.layer.get_layer( geom)

            feat = map_feat[geom]
            fields = map_fields[geom]

            render.add_feature_render(vlayer, feat, fields)

    def get_feat_cnt(self):
        return self.layer.get_feat_cnt()

    ############ handle_error
    def _get_params_reply(self, reply):
        keys = ["limit", "handle"]
        return dict(zip(
            keys,
            net_handler.get_qt_property(reply, keys)
        ))
    def _handle_error(self, err):
        chain_err = parse_exception_obj(err)
        if isinstance(chain_err, ChainInterrupt):
            e, idx = chain_err.args[0:2]
            if isinstance(e, net_handler.NetworkError): # retry only when network error, not timeout
                reply = e.args[-1]
                params = self._get_params_reply(reply)
                self.params_queue.gen_retry_params(**params)
                # start from beginning
                self.dispatch_parallel(n_parallel=1)
                return
        # otherwise emit error
        self.signal.error.emit(err)

    #threaded (parallel)
    def _dispatch_render(self, *parsed_feat):
        map_feat, map_fields = parsed_feat
        lst_args = [(
            geom,
            map_feat[geom],
            map_fields[geom]
            ) for geom in map_feat.keys()
        ]
        return lst_args
    def _render_single(self, geom, feat, fields):
        if not self.layer.is_valid( geom):
            vlayer=self.layer.show_ext_layer(geom)
        else:
            vlayer=self.layer.get_layer( geom)

        render.add_feature_render(vlayer, feat, fields)

########################
# Upload
########################

class InitUploadLayerController(ChainController):
    """ Prepare list of features of the input layer to be upload (added and removed)
    Stateful controller
    """
    def __init__(self, *a):
        super(InitUploadLayerController, self).__init__()
        self.pool = QThreadPool() # .globalInstance() will crash afterward
        self._config()
        
    def start(self, conn_info, vlayer, **kw):
        # assumed start() is called once # TODO: check if it is running
        if vlayer is None:
            raise InvalidXYZLayerError()
        self.conn_info = copy.deepcopy(conn_info) # upload
        self.kw = kw        
        super(InitUploadLayerController, self).start( vlayer)
    def start_args(self, args):
        a, kw = parse_qt_args(args)
        self.start(*a, **kw)
    def _config(self):
        self.config_fun([
            AsyncFun( layer_utils.get_feat_iter),
            WorkerFun( layer_utils.get_feat_upload_from_iter_args, self.pool),
            AsyncFun( self._setup_queue), 
        ])
    def _setup_queue(self, lst_added_feat, removed_feat):
        if len(lst_added_feat) == 0:
            self.signal.finished.emit()
        self.lst_added_feat = queue.SimpleQueue(lst_added_feat)
        return make_qt_args(self.get_conn_info(), self.lst_added_feat, **self.kw)
    def get_conn_info(self):
        return self.conn_info
        
class UploadLayerController(BaseLoop):
    """ Upload the list of features of the input layer (added and removed) to the destination space (conn_info)
    Stateful controller
    """
    def __init__(self, network, n_parallel=1):
        super(UploadLayerController, self).__init__()
        self.n_parallel = n_parallel
        self.pool = QThreadPool() # .globalInstance() will crash afterward
        self._config(network)
        self.feat_cnt = 0
    def _config(self, network):
        self.config_fun([
            NetworkFun( network.add_features), 
            WorkerFun( net_handler.on_received, self.pool),
            AsyncFun( self._process),
        ])
    def _process(self, obj, *a):
        self.feat_cnt += len(obj["features"])
    def get_feat_cnt(self):
        return self.feat_cnt
    def start(self, conn_info, lst_added_feat, **kw):
        self.conn_info = conn_info
        self.lst_added_feat = lst_added_feat

        self.fixed_params = dict(addTags=kw["tags"]) if "tags" in kw else dict()

        if self.count_active() == 0:
            super(UploadLayerController, self).reset()
        self.dispatch_parallel(n_parallel=self.n_parallel)
    def start_args(self, args):
        a, kw = parse_qt_args(args)
        self.start( *a, **kw)
    def _run_loop(self):
        if self.status == self.STOPPED: 
            self.signal.error.emit(ManualInterrupt())
            return 
        if not self.lst_added_feat.has_next():
            self._try_finish()
            return
            
        conn_info = self.get_conn_info()
        feat = self.lst_added_feat.get_params()
        LoopController.start(self, conn_info, feat, **self.fixed_params)
    def get_conn_info(self):
        return self.conn_info
    def _emit_finish(self):
        super()._emit_finish()
        
        token, space_id = self.conn_info.get_xyz_space()
        title = self.conn_info.get_("title")
        tags = self.fixed_params.get("addTags","")
        msg = "Space: %s - %s. Tags: %s. Token: %s"%(title, space_id, tags, token)
        self.signal.results.emit( make_qt_args(msg))

    def _handle_error(self, err):
        self.signal.error.emit(err)

class EditAddController(UploadLayerController):
    def start(self, conn_info, lst_added_feat, removed_feat, **kw):
        self.conn_info = conn_info
        self.lst_added_feat = queue.SimpleQueue(lst_added_feat)
        self.removed_feat = removed_feat
        # self.fixed_params = dict(addTags=kw["tags"]) if "tags" in kw else dict()

        if self.count_active() == 0:
            super(UploadLayerController, self).reset()
        self.dispatch_parallel(n_parallel=self.n_parallel)
    def _emit_finish(self):
        super(UploadLayerController, self)._emit_finish()
        
        self.signal.results.emit( make_qt_args(self.conn_info, self.removed_feat))

class EditRemoveController(ChainController):
    def __init__(self, network):
        super().__init__()
        self.pool = QThreadPool() # .globalInstance() will crash afterward
        self._config(network)
    def start(self, conn_info, removed_feat, **kw):
        if len(removed_feat) == 0:
            # self.signal.finished.emit()
            self.signal.results.emit(make_qt_args())
            return
        super().start(conn_info, removed_feat)
        # fixed_params = dict(addTags=kw["tags"]) if "tags" in kw else dict()
        # super().start(conn_info, removed_feat, **fixed_params)
    def start_args(self, args):
        a, kw = parse_qt_args(args)
        self.start( *a, **kw)
    def _config(self, network):
        self.config_fun([
            NetworkFun( network.del_features), 
            WorkerFun( net_handler.on_received, self.pool),
            # AsyncFun( self._process),
        ])