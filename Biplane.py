"""Biplane 模块主控文件：负责 Slicer UI 交互、截图流程、marker 排序与 2D/3D 几何运算调度。"""
import logging
import csv
import os
import itertools
import re
import time
from datetime import datetime
from typing import Annotated, Optional

import numpy as np
import vtk
import slicer
import qt
from BiplaneLib.dependencies import import_slicer_dependency

sitk = import_slicer_dependency("SimpleITK", "SimpleITK", install_on_missing=True)
cv2 = import_slicer_dependency("cv2", "opencv-python", install_on_missing=True)
from slicer.i18n import tr as _
from slicer.i18n import translate
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin
from slicer.parameterNodeWrapper import (
    parameterNodeWrapper,
    WithinRange,
)

from slicer import vtkMRMLScalarVolumeNode
import ScreenCapture
from vtk.util.numpy_support import vtk_to_numpy, numpy_to_vtk
from BiplaneLogics import *

#
# Biplane
#


class Biplane(ScriptedLoadableModule):
    """模块入口类：负责 Slicer 模块元信息注册。"""

    def __init__(self, parent):
        """初始化模块标题、分类、帮助文本与致谢信息。"""
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("Biplane")  # TODO: make this more human readable by adding spaces
        # TODO: set categories (folders where the module shows up in the module selector)
        self.parent.categories = [translate("qSlicerAbstractCoreModule", "Examples")]
        self.parent.dependencies = []  # TODO: add here list of module names that this module requires
        self.parent.contributors = ["John Doe (AnyWare Corp.)"]  # TODO: replace with "Firstname Lastname (Organization)"
        # TODO: update with short description of the module and a link to online module documentation
        # _() function marks text as translatable to other languages
        self.parent.helpText = _("""
This is an example of scripted loadable module bundled in an extension.
See more information in <a href="https://github.com/organization/projectname#Biplane">module documentation</a>.
""")
        # TODO: replace with organization, grant and thanks
        self.parent.acknowledgementText = _("""
This file was originally developed by Jean-Christophe Fillion-Robin, Kitware Inc., Andras Lasso, PerkLab,
and Steve Pieper, Isomics, Inc. and was partially funded by NIH grant 3P41RR013218-12S1.
""")


#
# BiplaneWidget
#


class BiplaneWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """主界面与交互控制类：包含拍摄、marker 排序、标定、重建与可视化流程。"""

    def __init__(self, parent=None) -> None:
        """初始化 Widget 状态、标定缓存、调试参数与输出目录。"""
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)  # needed for parameter node observation
        self.logic = None
        self._parameterNode = None
        self._parameterNodeGuiTag = None
        self._knifeObserverTag = None
        self.debugVisualization = False
        self.debugPlaneScale = 6.0
        self.debugRayScale = 10.0
        self.projectionMode = "perspective"
        self.perspectiveViewCalibs = {}
        self.orthographicViewCalibs = {}
        self._markersSorted = False
        self._angleObservedTransformNodes = []
        basePath = getattr(getattr(slicer, "app", None), "temporaryPath", os.path.expanduser("~/Desktop"))
        self.savePath = os.path.join(basePath, "Biplane")
        if not os.path.exists(self.savePath):
            os.makedirs(self.savePath)
        self.projectPath = os.path.dirname(os.path.abspath(__file__))
        self.experimentPath = os.path.join(self.projectPath, "experiment")
        os.makedirs(self.experimentPath, exist_ok=True)
        default_csv_path = os.path.join(self.experimentPath, "experiment_results.csv")
        self.csvFilePath = default_csv_path
        self.importCsvFilePath = default_csv_path
        self.importedExperimentRows = []
        self.importedExperimentFieldNames = []
        self.importedExperimentRowIndex = -1
        self.importedExperimentRestoreStatus = "Transforms: n/a"
        self.importCsvJumpRowValidator = None
        self.controlledPerturbationEnabled = False
        self.controlledPerturbationNoiseType = "marker-only"
        self.controlledPerturbationNoiseSigmaPx = 0.0
        self.controlledPerturbationRunCounter = 0
        self.controlledPerturbationRunState = None
        self.controlledPerturbationLastAppliedSummary = ""
        self.lastTreMmRaw = None
        self.lastReprojectionErrorPxRaw = None
        self.lastRayGapMmRaw = None
        self.markerSortMetrics = {}
        self.stepTimingsMs = {}
        self._suppressErrorDialogs = False

    def _error(self, message: str, detailedText: Optional[str] = None) -> None:
        """统一错误处理入口：优先使用 Slicer 弹窗，弹窗失败时写入日志。"""
        if getattr(self, "_suppressErrorDialogs", False):
            logging.error(message)
            if detailedText:
                logging.error(detailedText)
            return
        try:
            slicer.util.errorDisplay(message, detailedText=detailedText)
        except Exception:
            logging.error(message)
            if detailedText:
                logging.error(detailedText)

    def _require_markers_sorted(self) -> bool:
        """前置条件校验：确保 marker 排序、三视角标定数据及投影模式参数已完整准备。"""
        required_attrs = [
            "M2D3DPerspectiveMatrixsBig1",
            "M2D3DRigidMatrixsBig1",
            "M2D3DPerspectiveMatrixsSmall1",
            "M2D3DRigidMatrixsSmall1",
            "originBigMarker3D_Z",
            "originSmallMarker3D_Z",
            "bigMarker3DDic2",
            "smallMarker3DDic2",
        ]
        missing = [name for name in required_attrs if getattr(self, name, None) is None]
        if not self._markersSorted or missing:
            self._error("请先点击 markersSort")
            return False
        if self.projectionMode == "perspective":
            calibs = getattr(self, "perspectiveViewCalibs", None)
            if not isinstance(calibs, dict) or any(idx not in calibs for idx in (1, 2, 3)):
                self._error("Perspective mode calibration incomplete. Please click markersSort again")
                return False
        if self.projectionMode == "orthographic":
            calibs = getattr(self, "orthographicViewCalibs", None)
            if not isinstance(calibs, dict) or any(idx not in calibs for idx in (1, 2, 3)):
                self._error("Orthographic mode calibration incomplete. Please click markersSort again")
                return False
        return True

    def _getBodyVolumeNode(self):
        """获取 `_getBodyVolumeNode` 相关对象或计算结果。"""
        if self._parameterNode and getattr(self._parameterNode, "inputVolume", None):
            return self._parameterNode.inputVolume
        return slicer.mrmlScene.GetFirstNodeByClass("vtkMRMLScalarVolumeNode")

    def _getMarkersModelNode(self):
        """获取 `_getMarkersModelNode` 相关对象或计算结果。"""
        return slicer.mrmlScene.GetFirstNodeByName("markers")

    def _getThreeDView(self):
        """获取 `_getThreeDView` 相关对象或计算结果。"""
        lm = slicer.app.layoutManager()
        if not lm:
            return None
        threeDWidget = lm.threeDWidget(0)
        if not threeDWidget:
            return None
        return threeDWidget.threeDView()

    def _captureViewToFile(self, filepath: str) -> bool:
        """从当前 3D 视图截取图像并写入指定文件，返回是否截图成功。"""
        view = self._getThreeDView()
        if not view:
            self._error("3D 视图不可用，无法截图")
            return False
        view.forceRender()
        cap = ScreenCapture.ScreenCaptureLogic()
        cap.captureImageFromView(view, filepath)
        return os.path.exists(filepath)

    def _open_transforms_module(self) -> None:
        """尝试跳转到 Transforms 模块，若切换失败则给出错误提示。"""
        try:
            slicer.util.selectModule("Transforms")
        except Exception:
            self._error("Unable to switch to Transforms module")

    def _limit_display_nodes_for_shot(self, allowed_displayable_nodes):
        """拍摄前临时限制可见节点，仅保留允许列表中的对象可见，并返回恢复用备份。"""
        allowed_ids = {node.GetID() for node in allowed_displayable_nodes if node}
        display_nodes = slicer.mrmlScene.GetNodesByClass("vtkMRMLDisplayNode")
        display_nodes.InitTraversal()
        display_node = display_nodes.GetNextItemAsObject()

        visibility_backup = []
        while display_node:
            visibility = display_node.GetVisibility()
            visibility3d = display_node.GetVisibility3D() if hasattr(display_node, "GetVisibility3D") else None
            visibility_backup.append((display_node, visibility, visibility3d))

            displayable = display_node.GetDisplayableNode() if hasattr(display_node, "GetDisplayableNode") else None
            if not displayable or displayable.GetID() not in allowed_ids:
                display_node.SetVisibility(False)
                if visibility3d is not None:
                    display_node.SetVisibility3D(False)

            display_node = display_nodes.GetNextItemAsObject()

        return visibility_backup

    def _restore_display_nodes(self, visibility_backup):
        """按备份还原拍摄前各显示节点的 2D/3D 可见性状态。"""
        for display_node, visibility, visibility3d in visibility_backup:
            if display_node:
                display_node.SetVisibility(visibility)
                if visibility3d is not None and hasattr(display_node, "SetVisibility3D"):
                    display_node.SetVisibility3D(visibility3d)

    def _requireImage(self, filepath: str, label: str):
        """读取截图文件并校验有效性；文件缺失或解码失败时返回 None 并提示。"""
        if not os.path.exists(filepath):
            self._error(f"缺少 {label} 文件：{filepath}")
            return None
        img = cv2.imread(filepath, cv2.IMREAD_UNCHANGED)
        if img is None:
            self._error(f"无法读取 {label} 文件：{filepath}")
        return img

    def _get_black_center_from_volume(self, volumeNode):
        """获取 `_get_black_center_from_volume` 相关对象或计算结果。"""
        vtkImage = volumeNode.GetImageData() if volumeNode else None
        if vtkImage is None:
            return None
        vtk_data = vtkImage.GetPointData().GetScalars() if vtkImage.GetPointData() else None
        if vtk_data is None:
            return None
        dims = vtkImage.GetDimensions()
        if dims[0] == 0 or dims[1] == 0 or dims[2] == 0:
            return None
        np_data = vtk_to_numpy(vtk_data).reshape(tuple(reversed(dims)))
        slice2d = np_data[0]
        mask = np.isclose(slice2d, -100)
        if not np.any(mask):
            min_val = np.min(slice2d)
            mask = np.isclose(slice2d, min_val)
        if not np.any(mask):
            return None
        ys, xs = np.where(mask)
        if xs.size == 0:
            return None
        center_x = float(np.mean(xs))
        center_y = float(np.mean(ys))
        return (-center_x, -center_y, 0.0)

    def _get_volume_center(self, volumeNode):
        """获取 `_get_volume_center` 相关对象或计算结果。"""
        if volumeNode is None:
            return None
        bounds = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        volumeNode.GetRASBounds(bounds)
        return (
            (bounds[0] + bounds[1]) * 0.5,
            (bounds[2] + bounds[3]) * 0.5,
            (bounds[4] + bounds[5]) * 0.5,
        )

    def _show_black_center_marker(self, nodeName: str, viewNodeId: str, position):
        """在指定切片视图中创建或更新黑点中心标记点，并配置显示样式。"""
        markupsNode = slicer.mrmlScene.GetFirstNodeByName(nodeName)
        if markupsNode is None:
            markupsNode = slicer.vtkMRMLMarkupsFiducialNode()
            markupsNode.SetName(nodeName)
            slicer.mrmlScene.AddNode(markupsNode)
            markupsNode.CreateDefaultDisplayNodes()
        if markupsNode.GetNumberOfControlPoints() < 1:
            markupsNode.AddControlPoint(position)
        else:
            markupsNode.SetNthControlPointPosition(0, position)
        displayNode = markupsNode.GetDisplayNode()
        if displayNode:
            displayNode.SetVisibility(True)
            displayNode.SetViewNodeIDs([viewNodeId])
            displayNode.SetVisibility3D(False)
            displayNode.SetPointLabelsVisibility(False)
            displayNode.SetGlyphScale(1.0)
            displayNode.SetSelectedColor([1.0, 0.2, 0.0])
            displayNode.SetColor(1.0, 0.2, 0.0)

    def _get_slice_view_center(self, viewNodeId: str):
        """获取 `_get_slice_view_center` 相关对象或计算结果。"""
        sliceNode = slicer.mrmlScene.GetNodeByID(viewNodeId)
        if sliceNode is None:
            return None
        layoutManager = slicer.app.layoutManager()
        if layoutManager is None:
            return None
        sliceWidget = layoutManager.sliceWidget(sliceNode.GetLayoutName())
        if sliceWidget is None:
            return None
        sliceView = sliceWidget.sliceView()
        if sliceView is None:
            return None
        width = float(sliceView.width)
        height = float(sliceView.height)
        xy_to_ras = sliceNode.GetXYToRAS()
        ras = [0.0, 0.0, 0.0, 1.0]
        xy_to_ras.MultiplyPoint([width * 0.5, height * 0.5, 0.0, 1.0], ras)
        return (ras[0], ras[1], ras[2])

    def _ensure_center_fiducial(self, nodeName: str, viewNodeId: str, color):
        """确保 `_ensure_center_fiducial` 所需的节点或状态已准备就绪。"""
        markupsNode = slicer.mrmlScene.GetFirstNodeByName(nodeName)
        if markupsNode is not None:
            return
        markupsNode = slicer.vtkMRMLMarkupsFiducialNode()
        markupsNode.SetName(nodeName)
        slicer.mrmlScene.AddNode(markupsNode)
        markupsNode.CreateDefaultDisplayNodes()
        center = self._get_slice_view_center(viewNodeId) or (0.0, 0.0, 0.0)
        markupsNode.AddControlPoint(center)
        displayNode = markupsNode.GetDisplayNode()
        if displayNode:
            displayNode.SetVisibility(True)
            displayNode.SetViewNodeIDs([viewNodeId])
            displayNode.SetVisibility3D(False)
            displayNode.SetPointLabelsVisibility(False)
            displayNode.SetGlyphScale(3.0)
            displayNode.SetSelectedColor(color)
            displayNode.SetColor(color)

    def _set_selector_current_node(self, selector, node):
        """设置 `_set_selector_current_node` 相关状态或界面显示。"""
        if selector is None or node is None:
            return
        if hasattr(selector, "setCurrentNode"):
            selector.setCurrentNode(node)
        elif hasattr(selector, "setCurrentNodeID"):
            selector.setCurrentNodeID(node.GetID())

    def _plane_normal_from_marker_dict(self, marker_dict):
        """从 marker 字典中提取 1/2/3 号点计算平面法向量，结果归一化后返回。"""
        if not marker_dict:
            return None
        p1 = marker_dict.get(1)
        p2 = marker_dict.get(2)
        p3 = marker_dict.get(3)
        if p1 is None or p2 is None or p3 is None:
            return None
        v1 = np.array(p2, dtype=float) - np.array(p1, dtype=float)
        v2 = np.array(p3, dtype=float) - np.array(p1, dtype=float)
        normal = np.cross(v1, v2)
        norm = np.linalg.norm(normal)
        if norm == 0.0:
            return None
        return normal / norm

    def _compute_plane_angle_deg(self, marker_dict_a, marker_dict_b):
        """计算 `_compute_plane_angle_deg` 相关的几何或标定结果。"""
        n1 = self._plane_normal_from_marker_dict(marker_dict_a)
        n2 = self._plane_normal_from_marker_dict(marker_dict_b)
        if n1 is None or n2 is None:
            return None
        dot = float(np.clip(np.dot(n1, n2), -1.0, 1.0))
        return float(np.degrees(np.arccos(abs(dot))))

    def _get_big_marker_plane_dict(self, index: int):
        """获取 `_get_big_marker_plane_dict` 相关对象或计算结果。"""
        transform_names = {
            1: "LinearTransform",
            2: "LinearTransform_1",
            3: "LinearTransform_2",
        }
        transform_name = transform_names.get(index)
        if not transform_name:
            return None
        transform_node = slicer.mrmlScene.GetFirstNodeByName(transform_name)
        if transform_node is None:
            return None
        marker_dict = self.generateMarkers.getMarkerTransform(
            transform_node,
            self.generateMarkers.bigMarker3DDic,
        )
        return marker_dict

    def _on_marker_transform_modified(self, caller=None, event=None):
        """Transform changed: refresh plane-angle displays when marker setup is ready."""
        if not self._markersSorted:
            return
        self._update_shot2_angle_display()
        self._update_shot3_angle_display()

    def _observe_transform_nodes_for_angles(self) -> None:
        """Attach observers to LinearTransform nodes for live angle refresh."""
        for node in self._angleObservedTransformNodes:
            try:
                self.removeObserver(node, vtk.vtkCommand.ModifiedEvent, self._on_marker_transform_modified)
            except Exception:
                pass
        self._angleObservedTransformNodes = []

        for name in ("LinearTransform", "LinearTransform_1", "LinearTransform_2"):
            node = slicer.mrmlScene.GetFirstNodeByName(name)
            if node is None:
                continue
            self.addObserver(node, vtk.vtkCommand.ModifiedEvent, self._on_marker_transform_modified)
            self._angleObservedTransformNodes.append(node)

    def _set_angle_display(self, label_widget, line_edit, angle_value):
        """设置 `_set_angle_display` 相关状态或界面显示。"""
        if label_widget is not None:
            label_widget.setVisible(True)
        if line_edit is not None:
            line_edit.setVisible(True)
            if angle_value is None:
                line_edit.setText("n/a")
            else:
                line_edit.setText(f"{angle_value:.2f}")

    def _update_shot2_angle_display(self):
        """更新 `_update_shot2_angle_display` 对应的界面或内部状态。"""
        angle = self._compute_plane_angle_deg(
            self._get_big_marker_plane_dict(1),
            self._get_big_marker_plane_dict(2),
        )
        self._set_angle_display(self.ui.labelShot2Angle, self.ui.shot2AngleLineEdit, angle)

    def _update_shot3_angle_display(self):
        """更新 `_update_shot3_angle_display` 对应的界面或内部状态。"""
        angle_m3_m1 = self._compute_plane_angle_deg(
            self._get_big_marker_plane_dict(3),
            self._get_big_marker_plane_dict(1),
        )
        angle_m3_m2 = self._compute_plane_angle_deg(
            self._get_big_marker_plane_dict(3),
            self._get_big_marker_plane_dict(2),
        )
        self._set_angle_display(self.ui.labelShot3Angle1, self.ui.shot3Angle1LineEdit, angle_m3_m1)
        self._set_angle_display(self.ui.labelShot3Angle2, self.ui.shot3Angle2LineEdit, angle_m3_m2)

    def setup(self) -> None:
        """构建界面、绑定事件、初始化逻辑对象与参数节点观察。"""
        ScriptedLoadableModuleWidget.setup(self)

        # Load widget from .ui file (created by Qt Designer).
        # Additional widgets can be instantiated manually and added to self.layout.
        uiWidget = slicer.util.loadUI(self.resourcePath("UI/Biplane.ui"))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)

        # Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
        # "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
        # "setMRMLScene(vtkMRMLScene*)" slot.
        uiWidget.setMRMLScene(slicer.mrmlScene)

        self.ui.copySourceSelector.setMRMLScene(slicer.mrmlScene)
        self.ui.copyTargetSelector.setMRMLScene(slicer.mrmlScene)

        # Create logic class. Logic implements all computations that should be possible to run
        # in batch mode, without a graphical user interface.
        self.logic = BiplaneLogic()
        self.generateMarkers = GenerateMarkers()
        self.markerSource = self.generateMarkers.marksSource
        # Connections

        # These connections ensure that we update parameter node when scene is closed
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)

        # Buttons
        self.ui.showVolumeButton.connect("clicked(bool)", self.onShowVolumeButton)
        self.ui.showMarkerButton.connect("clicked(bool)", self.onShowMarkerButton)
        self.ui.showTestPointButton.connect("clicked(bool)", self.onShowTestPointButton)
        self.ui.orthographicProjectionButton.connect("clicked(bool)", self.onOrthographicProjectionButton)
        self.ui.perspectiveProjectionButton.connect("clicked(bool)", self.onPerspectiveProjectionButton)

        self.ui.shot1AllButton.connect("clicked(bool)", self.onShot1AllButton)
        self.ui.shot2AllButton.connect("clicked(bool)", self.onShot2AllButton)
        self.ui.shot3AllButton.connect("clicked(bool)", self.onShot3AllButton)

        self.ui.markers1Button.connect("clicked(bool)", self.onMarkers1Button)
        self.ui.markers2Button.connect("clicked(bool)", self.onMarkers2Button)
        self.ui.markers3Button.connect("clicked(bool)", self.onMarkers3Button)
        self.ui.transformsButton.connect("clicked(bool)", self.onOpenTransforms)

        self.ui.blackCenterButton.connect("clicked(bool)", self.onBlackCenterButton)
        self.ui.markersSortButton.connect("clicked(bool)", self.onMarkersSortButton)
        self.ui.copyPointButton.connect("clicked(bool)", self.onCopyMarkerPoint)
        self.ui.copyBlackCenter1Button.connect("clicked(bool)", self.onCopyBlackCenter1)
        self.ui.copyBlackCenter2Button.connect("clicked(bool)", self.onCopyBlackCenter2)
        
        self.ui.redPushButton.connect("clicked(bool)", self.onTwoD2ThreeDRed)
        self.ui.greenPushButton.connect("clicked(bool)", self.onTwoD2ThreeDGreen)

        self.ui.tracingPushButton.connect("clicked(bool)", self.onTracing)
        self.ui.showKnifePushButton.connect("clicked(bool)", self.onShowKnifeButton)

        self.ui.calculateTREButton.connect("clicked(bool)", self.onCalculateTRE)
        self.ui.calculateReprojectionButton.connect("clicked(bool)", self.onCalculateReprojectionError)
        self.ui.controlledPerturbationNoiseTypeComboBox.connect(
            "currentIndexChanged(int)", self.onControlledPerturbationOptionsChanged
        )
        self.ui.controlledPerturbationNoiseSigmaComboBox.connect(
            "currentIndexChanged(int)", self.onControlledPerturbationOptionsChanged
        )
        self.ui.runControlledPerturbationButton.connect("clicked(bool)", self.onRunControlledPerturbation)
        self.ui.runControlledPerturbationWorkflowButton.connect(
            "clicked(bool)", self.onRunControlledPerturbationWorkflow
        )
        self.ui.runControlledPerturbationBatchWorkflowButton.connect(
            "clicked(bool)", self.onRunControlledPerturbationBatchWorkflow
        )
        self.ui.browseCsvPathButton.connect("clicked(bool)", self.onBrowseCsvPath)
        self.ui.saveResultsCsvButton.connect("clicked(bool)", self.onSaveResultsCsv)
        self.ui.browseImportCsvPathButton.connect("clicked(bool)", self.onBrowseImportCsvPath)
        self.ui.loadImportCsvButton.connect("clicked(bool)", self.onLoadImportCsv)
        self.ui.importCsvPrevRowButton.connect("clicked(bool)", self.onImportCsvPreviousRow)
        self.ui.importCsvNextRowButton.connect("clicked(bool)", self.onImportCsvNextRow)
        if hasattr(self.ui, "importCsvJumpRowLineEdit") and self.ui.importCsvJumpRowLineEdit is not None:
            self.importCsvJumpRowValidator = qt.QIntValidator(1, 1, self.ui.importCsvJumpRowLineEdit)
            self.ui.importCsvJumpRowLineEdit.setValidator(self.importCsvJumpRowValidator)
            self.ui.importCsvJumpRowLineEdit.connect("returnPressed()", self.onImportCsvJumpToRow)
        if hasattr(self.ui, "importCsvJumpRowButton") and self.ui.importCsvJumpRowButton is not None:
            self.ui.importCsvJumpRowButton.connect("clicked(bool)", self.onImportCsvJumpToRow)

        self.ui.debugVisCheckBox.connect("toggled(bool)", self.onDebugVisToggle)
        self.ui.debugPlaneScaleSpinBox.connect("valueChanged(double)", self.onDebugPlaneScaleChanged)
        self.ui.debugRayScaleSpinBox.connect("valueChanged(double)", self.onDebugRayScaleChanged)

        self.debugPlaneScale = float(self.ui.debugPlaneScaleSpinBox.value)
        self.debugRayScale = float(self.ui.debugRayScaleSpinBox.value)

        angle_line_edits = [
            self.ui.shot2AngleLineEdit,
            self.ui.shot3Angle1LineEdit,
            self.ui.shot3Angle2LineEdit,
        ]
        for line_edit in angle_line_edits:
            if line_edit is not None:
                line_edit.setText("")

        if hasattr(self.ui, "csvSavePathLineEdit") and self.ui.csvSavePathLineEdit is not None:
            self.ui.csvSavePathLineEdit.setText(self.csvFilePath)
        if hasattr(self.ui, "importCsvPathLineEdit") and self.ui.importCsvPathLineEdit is not None:
            self.ui.importCsvPathLineEdit.setText(self.importCsvFilePath)
        self._initialize_controlled_perturbation_ui()
        self._update_import_csv_status()

        self.ui.orthographicProjectionButton.setChecked(False)
        self.ui.perspectiveProjectionButton.setChecked(True)
        self._updateProjectionModeStatusLabel()
        self._applyProjectionModeToView()

        # Make sure parameter node is initialized (needed for module reload)
        self.initializeParameterNode()

    def cleanup(self) -> None:
        """模块销毁阶段回调：移除本 Widget 注册的所有事件观察器。"""
        self.removeObservers()

    def enter(self) -> None:
        """模块进入回调：确保参数节点与变换节点就绪，同步视图投影与显示风格。"""
        # Make sure parameter node exists and observed
        self.initializeParameterNode()

        self._ensureLinearTransformNodes()
        self._applyProjectionModeToView()

        viewNode = slicer.mrmlScene.GetNodeByID("vtkMRMLViewNode1")
        viewNode.SetBackgroundColor(1, 1, 1)
        viewNode.SetBackgroundColor2(1, 1, 1)
        viewNode.SetBoxVisible(False)
        viewNode.SetAxisLabelsVisible(False)

    def _applyProjectionModeToView(self) -> None:
        """将 `_applyProjectionModeToView` 相关配置应用到当前场景。"""
        lm = slicer.app.layoutManager()
        if not lm:
            return
        threeDWidget = lm.threeDWidget(0)
        if not threeDWidget:
            return
        threeDControllerWidget = threeDWidget.threeDController()
        if not threeDControllerWidget:
            return
        use_orthographic = self.projectionMode == "orthographic"
        threeDControllerWidget.setOrthographicModeEnabled(use_orthographic)

    def _updateProjectionModeStatusLabel(self) -> None:
        """更新 `_updateProjectionModeStatusLabel` 对应的界面或内部状态。"""
        if not hasattr(self.ui, "projectionModeStatusLabel"):
            return
        mode_text = "Orthographic" if self.projectionMode == "orthographic" else "Perspective"
        self.ui.projectionModeStatusLabel.setText(f"Current mode: {mode_text}")

    def _setProjectionMode(self, mode: str) -> None:
        """设置 `_setProjectionMode` 相关状态或界面显示。"""
        if mode not in ("orthographic", "perspective"):
            return
        self._reset_controlled_perturbation_run_state(clear_summary=True)
        self.projectionMode = mode
        self.ui.orthographicProjectionButton.setChecked(mode == "orthographic")
        self.ui.perspectiveProjectionButton.setChecked(mode == "perspective")
        self._updateProjectionModeStatusLabel()
        self._applyProjectionModeToView()

        if self._markersSorted:
            ok = self.initMarkers()
            if not ok:
                return
            self.initLightVec()

    def onOrthographicProjectionButton(self):
        """界面回调：执行 `onOrthographicProjectionButton` 对应的交互处理流程。"""
        self._setProjectionMode("orthographic")

    def onPerspectiveProjectionButton(self):
        """界面回调：执行 `onPerspectiveProjectionButton` 对应的交互处理流程。"""
        self._setProjectionMode("perspective")

    def _get_shot_node_by_index(self, view_index: int):
        """获取 `_get_shot_node_by_index` 相关对象或计算结果。"""
        return slicer.mrmlScene.GetFirstNodeByName(f"shot{view_index}")

    def _get_shot_image_size(self, view_index: int):
        """获取 `_get_shot_image_size` 相关对象或计算结果。"""
        shot_node = self._get_shot_node_by_index(view_index)
        if not shot_node or not shot_node.GetImageData():
            return None
        dims = shot_node.GetImageData().GetDimensions()
        if dims[0] <= 0 or dims[1] <= 0:
            return None
        return int(dims[0]), int(dims[1])

    def _get_current_3d_camera_view_angle(self) -> float:
        """获取 `_get_current_3d_camera_view_angle` 相关对象或计算结果。"""
        view = self._getThreeDView()
        if not view:
            return 30.0
        render_window = view.renderWindow() if hasattr(view, "renderWindow") else None
        if not render_window:
            return 30.0
        renderer = render_window.GetRenderers().GetFirstRenderer() if render_window.GetRenderers() else None
        if not renderer:
            return 30.0
        camera = renderer.GetActiveCamera()
        if not camera:
            return 30.0
        return float(camera.GetViewAngle())

    def _get_view_marker_pairs(
        self,
        view_index: int,
        swap_big_23: bool = False,
        swap_small_23: bool = False,
        marker_views: Optional[dict] = None,
    ):
        """获取 `_get_view_marker_pairs` 相关对象或计算结果。"""
        if marker_views is not None and view_index in marker_views:
            view_marker_data = marker_views[view_index]
            big_2d = view_marker_data["big"]
            small_2d = view_marker_data["small"]
        else:
            big_2d = getattr(self, f"bigMarkersSort{view_index}")
            small_2d = getattr(self, f"smallMarkersSort{view_index}")
        big_3d = getattr(self, f"bigMarker3DDic{view_index}")
        small_3d = getattr(self, f"smallMarker3DDic{view_index}")

        image_points = []
        object_points = []

        def _point_by_label(markers_2d: dict, label: int, marker_kind: str):
            point = get_key_by_value(markers_2d, label)
            if point is None:
                raise ValueError(f"view{view_index} missing {marker_kind} marker label {label}")
            return point[0:2]

        big_object_labels = (1, 2, 3, 4, 5)
        big_image_labels = (1, 3, 2, 4, 5) if swap_big_23 else big_object_labels
        for object_label, image_label in zip(big_object_labels, big_image_labels):
            image_points.append(_point_by_label(big_2d, image_label, "big"))
            object_points.append(big_3d[object_label])

        small_object_labels = (1, 2, 3, 4, 5)
        small_image_labels = (1, 3, 2, 4, 5) if swap_small_23 else small_object_labels
        for object_label, image_label in zip(small_object_labels, small_image_labels):
            image_points.append(_point_by_label(small_2d, image_label, "small"))
            object_points.append(small_3d[object_label])

        return np.array(object_points, dtype=np.float64), np.array(image_points, dtype=np.float64)

    def _solve_perspective_calibrations(self, marker_views: Optional[dict] = None) -> dict:
        """Solve perspective calibrations from the provided clean or perturbed marker correspondences."""
        view_angle = self._get_current_3d_camera_view_angle()
        max_rms_px = 5.0
        calibs = {}
        for idx in (1, 2, 3):
            image_size = self._get_shot_image_size(idx)
            if not image_size:
                raise ValueError(f"shot{idx} image size unavailable")
            image_width, image_height = image_size
            camera_matrix, dist_coeffs = self.logic.buildCameraIntrinsics(image_width, image_height, view_angle)

            best = None
            for swap_big_23 in (False, True):
                for swap_small_23 in (False, True):
                    object_points, image_points = self._get_view_marker_pairs(
                        idx,
                        swap_big_23=swap_big_23,
                        swap_small_23=swap_small_23,
                        marker_views=marker_views,
                    )
                    for flip_x in (False, True):
                        for flip_y in (False, True):
                            img_pts_px = np.vstack([
                                self._slicer2d_to_pixel_raw(p, image_width, image_height, flip_x, flip_y)
                                for p in image_points
                            ])
                            rvec, tvec = self.logic.estimateCameraPosePnP(
                                object_points,
                                img_pts_px,
                                camera_matrix,
                                dist_coeffs,
                            )
                            proj, _ = cv2.projectPoints(
                                object_points.reshape(-1, 1, 3),
                                np.array(rvec, dtype=np.float64),
                                np.array(tvec, dtype=np.float64),
                                np.array(camera_matrix, dtype=np.float64),
                                np.array(dist_coeffs, dtype=np.float64),
                            )
                            proj = proj.reshape(-1, 2)
                            rms = float(np.sqrt(np.mean(np.sum((proj - img_pts_px) ** 2, axis=1))))
                            if best is None or rms < best[0]:
                                best = (rms, flip_x, flip_y, swap_big_23, swap_small_23, rvec, tvec)

            if best is None:
                raise ValueError(f"view{idx} solvePnP calibration failed")
            rms, flip_x, flip_y, swap_big_23, swap_small_23, rvec, tvec = best
            if rms > max_rms_px:
                raise ValueError(
                    f"view{idx} PnP reproj RMS too high ({rms:.3f}px > {max_rms_px:.1f}px); "
                    "check marker ordering/transforms and rerun markersSort"
                )

            logging.info(
                "PnP calib view%d: reproj RMS=%.3fpx, flip_x=%s, flip_y=%s, swap_big_23=%s, "
                "swap_small_23=%s, fov=%.2fdeg",
                idx,
                rms,
                flip_x,
                flip_y,
                swap_big_23,
                swap_small_23,
                view_angle,
            )

            calibs[idx] = {
                "K": camera_matrix,
                "dist": dist_coeffs,
                "rvec": rvec,
                "tvec": tvec,
                "w": int(image_width),
                "h": int(image_height),
                "flip_x": bool(flip_x),
                "flip_y": bool(flip_y),
                "swap_big_23": bool(swap_big_23),
                "swap_small_23": bool(swap_small_23),
                "reproj_rms_px": float(rms),
                "view_angle_deg": float(view_angle),
            }

        return calibs

    def _solve_orthographic_calibrations(self, marker_views: Optional[dict] = None) -> dict:
        """Solve orthographic calibrations from the provided clean or perturbed marker correspondences."""
        max_rms_px = 5.0
        view_angle = self._get_current_3d_camera_view_angle()
        calibs = {}
        for idx in (1, 2, 3):
            image_size = self._get_shot_image_size(idx)
            if not image_size:
                raise ValueError(f"shot{idx} image size unavailable")
            image_width, image_height = image_size

            best = None
            for swap_big_23 in (False, True):
                for swap_small_23 in (False, True):
                    object_points, image_points = self._get_view_marker_pairs(
                        idx,
                        swap_big_23=swap_big_23,
                        swap_small_23=swap_small_23,
                        marker_views=marker_views,
                    )
                    X = np.hstack([object_points, np.ones((object_points.shape[0], 1), dtype=np.float64)])

                    for flip_x in (False, True):
                        for flip_y in (False, True):
                            img_pts_px = np.vstack([
                                self._slicer2d_to_pixel_raw(p, image_width, image_height, flip_x, flip_y)
                                for p in image_points
                            ])
                            u = img_pts_px[:, 0]
                            v = img_pts_px[:, 1]

                            pu, _, _, _ = np.linalg.lstsq(X, u, rcond=None)
                            pv, _, _, _ = np.linalg.lstsq(X, v, rcond=None)
                            P = np.vstack([pu, pv])

                            proj = X @ P.T
                            rms = float(np.sqrt(np.mean(np.sum((proj - img_pts_px) ** 2, axis=1))))
                            if best is None or rms < best[0]:
                                best = (rms, flip_x, flip_y, swap_big_23, swap_small_23, P)

            if best is None:
                raise ValueError(f"view{idx} orthographic calib failed")
            rms, flip_x, flip_y, swap_big_23, swap_small_23, P = best
            if rms > max_rms_px:
                raise ValueError(
                    f"view{idx} orthographic reproj RMS too high ({rms:.3f}px > {max_rms_px:.1f}px); "
                    "check marker ordering/transforms and rerun markersSort"
                )

            A = np.array(P[:, :3], dtype=np.float64)
            t = np.array(P[:, 3], dtype=np.float64)
            d = np.cross(A[0, :], A[1, :])
            dn = np.linalg.norm(d)
            if np.isclose(dn, 0.0):
                raise ValueError(f"view{idx} orthographic direction is degenerate")
            d = d / dn

            logging.info(
                "Ortho calib view%d: reproj RMS=%.3fpx, flip_x=%s, flip_y=%s, swap_big_23=%s, swap_small_23=%s",
                idx,
                rms,
                flip_x,
                flip_y,
                swap_big_23,
                swap_small_23,
            )

            calibs[idx] = {
                "P": P,
                "A": A,
                "t": t,
                "d": d,
                "w": int(image_width),
                "h": int(image_height),
                "flip_x": bool(flip_x),
                "flip_y": bool(flip_y),
                "swap_big_23": bool(swap_big_23),
                "swap_small_23": bool(swap_small_23),
                "reproj_rms_px": float(rms),
                "view_angle_deg": float(view_angle),
            }

        return calibs

    def _slicer2d_to_pixel_raw(self, point2d_slicer: np.array, image_width: int, image_height: int, flip_x: bool, flip_y: bool):
        """执行 `_slicer2d_to_pixel_raw` 所对应的坐标系转换或投影运算。"""
        u = -float(point2d_slicer[0])
        v = -float(point2d_slicer[1])
        if flip_x:
            u = (float(image_width) - 1.0) - u
        if flip_y:
            v = (float(image_height) - 1.0) - v
        return np.array([u, v], dtype=np.float64)

    def _pixel_to_slicer2d_raw(self, pixel2d: np.array, image_width: int, image_height: int, flip_x: bool, flip_y: bool):
        """执行 `_pixel_to_slicer2d_raw` 所对应的坐标系转换或投影运算。"""
        u = float(pixel2d[0])
        v = float(pixel2d[1])
        if flip_x:
            u = (float(image_width) - 1.0) - u
        if flip_y:
            v = (float(image_height) - 1.0) - v
        x = -u
        y = -v
        return np.array([x, y], dtype=np.float64)

    def _get_view_transform_node(self, view_index: int):
        """获取 `_get_view_transform_node` 相关对象或计算结果。"""
        transform_name_by_view = {
            1: "LinearTransform",
            2: "LinearTransform_1",
            3: "LinearTransform_2",
        }
        transform_name = transform_name_by_view.get(view_index)
        if transform_name is None:
            return None
        return slicer.mrmlScene.GetFirstNodeByName(transform_name)

    def _sort_detected_markers_for_view(self, view_index: int, marker_sort_logic):
        """对单视角 marker 检测点进行编号排序，综合单应与 PnP 重投影误差选择最优映射。"""
        labels = (1, 2, 3, 4, 5)
        if len(marker_sort_logic.big) != 5 or len(marker_sort_logic.small) != 5:
            raise ValueError(
                f"view{view_index} expects 5 big + 5 small detections, "
                f"got big={len(marker_sort_logic.big)}, small={len(marker_sort_logic.small)}"
            )

        image_size = self._get_shot_image_size(view_index)
        if not image_size:
            raise ValueError(f"view{view_index} image size unavailable")
        image_width, image_height = image_size

        transform_node = self._get_view_transform_node(view_index)
        if transform_node is None:
            raise ValueError(f"view{view_index} transform node not found")

        big_marker_3d = self.generateMarkers.getMarkerTransform(
            transform_node,
            self.generateMarkers.bigMarker3DDic,
        )
        small_marker_3d = self.generateMarkers.getMarkerTransform(
            transform_node,
            self.generateMarkers.smallMarker3DDic,
        )

        object_points = np.array(
            [big_marker_3d[label] for label in labels] + [small_marker_3d[label] for label in labels],
            dtype=np.float64,
        )

        raw_big = [tuple(map(float, p[0:2])) for p in marker_sort_logic.big]
        raw_small = [tuple(map(float, p[0:2])) for p in marker_sort_logic.small]
        big_slicer = [np.array([-p[0], -p[1]], dtype=np.float64) for p in raw_big]
        small_slicer = [np.array([-p[0], -p[1]], dtype=np.float64) for p in raw_small]

        view_angle = self._get_current_3d_camera_view_angle()
        camera_matrix, dist_coeffs = self.logic.buildCameraIntrinsics(image_width, image_height, view_angle)

        perms = list(itertools.permutations(range(5)))
        template_big_xy = np.array([big_marker_3d[label][0:2] for label in labels], dtype=np.float64)
        template_small_xy = np.array([small_marker_3d[label][0:2] for label in labels], dtype=np.float64)
        template_big_xy_reshape = template_big_xy.reshape(-1, 1, 2)
        template_small_xy_reshape = template_small_xy.reshape(-1, 1, 2)
        candidate_top_k = 6

        def _rank_permutations_with_homography(template_xy, template_xy_reshape, detected_px):
            scored = []
            for perm in perms:
                target = np.array([detected_px[i] for i in perm], dtype=np.float64)
                H, _ = cv2.findHomography(template_xy, target, method=0)
                if H is None:
                    continue
                projected = cv2.perspectiveTransform(template_xy_reshape, H).reshape(-1, 2)
                rms = float(np.sqrt(np.mean(np.sum((projected - target) ** 2, axis=1))))
                scored.append((rms, perm))
            scored.sort(key=lambda x: x[0])
            if not scored:
                return []
            return scored[:candidate_top_k]

        best = None
        second = None

        for flip_x in (False, True):
            for flip_y in (False, True):
                big_px = [
                    self._slicer2d_to_pixel_raw(p, image_width, image_height, flip_x, flip_y)
                    for p in big_slicer
                ]
                small_px = [
                    self._slicer2d_to_pixel_raw(p, image_width, image_height, flip_x, flip_y)
                    for p in small_slicer
                ]

                big_candidates = _rank_permutations_with_homography(
                    template_big_xy,
                    template_big_xy_reshape,
                    big_px,
                )
                small_candidates = _rank_permutations_with_homography(
                    template_small_xy,
                    template_small_xy_reshape,
                    small_px,
                )
                if not big_candidates or not small_candidates:
                    continue

                big_perm_points = {
                    perm: np.array([big_px[i] for i in perm], dtype=np.float64)
                    for _, perm in big_candidates
                }
                small_perm_points = {
                    perm: np.array([small_px[i] for i in perm], dtype=np.float64)
                    for _, perm in small_candidates
                }

                for _, big_perm in big_candidates:
                    big_points = big_perm_points[big_perm]
                    for _, small_perm in small_candidates:
                        img_pts_px = np.vstack([big_points, small_perm_points[small_perm]])
                        try:
                            success, rvec, tvec = cv2.solvePnP(
                                object_points,
                                img_pts_px,
                                camera_matrix,
                                dist_coeffs,
                                flags=cv2.SOLVEPNP_EPNP,
                            )
                        except cv2.error:
                            continue
                        if not success:
                            continue

                        projected, _ = cv2.projectPoints(
                            object_points.reshape(-1, 1, 3),
                            np.array(rvec, dtype=np.float64),
                            np.array(tvec, dtype=np.float64),
                            np.array(camera_matrix, dtype=np.float64),
                            np.array(dist_coeffs, dtype=np.float64),
                        )
                        projected = projected.reshape(-1, 2)
                        rms = float(np.sqrt(np.mean(np.sum((projected - img_pts_px) ** 2, axis=1))))

                        candidate = (rms, flip_x, flip_y, big_perm, small_perm)
                        if best is None or rms < best[0]:
                            second = best
                            best = candidate
                        elif second is None or rms < second[0]:
                            second = candidate

        if best is None:
            raise ValueError(f"view{view_index} failed to find a valid marker assignment")

        rms, flip_x, flip_y, best_big_perm, best_small_perm = best
        if second is None:
            logging.info(
                "Marker assignment view%d: RMS=%.3fpx, flip_x=%s, flip_y=%s",
                view_index,
                rms,
                flip_x,
                flip_y,
            )
        else:
            second_rms = second[0]
            logging.info(
                "Marker assignment view%d: best RMS=%.3fpx, second=%.3fpx, gap=%.3fpx, flip_x=%s, flip_y=%s",
                view_index,
                rms,
                second_rms,
                second_rms - rms,
                flip_x,
                flip_y,
            )

        second_rms = second[0] if second is not None else ""
        rms_gap = (second_rms - rms) if second is not None else ""
        self.markerSortMetrics[view_index] = {
            "rms_px": float(rms),
            "second_rms_px": float(second_rms) if second_rms != "" else "",
            "rms_gap_px": float(rms_gap) if rms_gap != "" else "",
            "flip_x": int(bool(flip_x)),
            "flip_y": int(bool(flip_y)),
        }

        big_sorted = {}
        small_sorted = {}
        for label, detected_index in zip(labels, best_big_perm):
            big_sorted[raw_big[detected_index]] = label
        for label, detected_index in zip(labels, best_small_perm):
            small_sorted[raw_small[detected_index]] = label
        return big_sorted, small_sorted

    def _compute_perspective_calibrations(self) -> bool:
        """计算三个视角的透视标定参数（K/dist/rvec/tvec 及翻转、编号交换状态）。"""
        start_time = time.perf_counter()
        try:
            self.perspectiveViewCalibs = self._solve_perspective_calibrations()
            return True
        except Exception as e:
            self.perspectiveViewCalibs = {}
            self._error("Perspective solvePnP calibration failed", detailedText=str(e))
            return False
        finally:
            self.stepTimingsMs["perspective_calibration_ms"] = round(
                (time.perf_counter() - start_time) * 1000.0,
                3,
            )

    def _compute_orthographic_calibrations(self) -> bool:
        """计算三个视角的正交标定参数（2x4 投影模型、平面方向向量与异常阈值校验）。"""
        start_time = time.perf_counter()
        try:
            self.orthographicViewCalibs = self._solve_orthographic_calibrations()
            return True
        except Exception as e:
            self.orthographicViewCalibs = {}
            self._error("Orthographic calibration failed", detailedText=str(e))
            return False
        finally:
            self.stepTimingsMs["orthographic_calibration_ms"] = round(
                (time.perf_counter() - start_time) * 1000.0,
                3,
            )

    def _ortho_pixel_to_world_ray(self, view_index: int, point2d_slicer: np.array):
        """执行 `_ortho_pixel_to_world_ray` 所对应的坐标系转换或投影运算。"""
        calib = self._get_active_orthographic_calibrations().get(view_index)
        if calib is None:
            raise ValueError(f"Missing orthographic calibration for view{view_index}")

        p_px = self._slicer2d_to_pixel_raw(
            point2d_slicer,
            calib["w"],
            calib["h"],
            calib["flip_x"],
            calib["flip_y"],
        )

        A = calib["A"]
        t = calib["t"]
        b = np.array([p_px[0] - t[0], p_px[1] - t[1]], dtype=np.float64)

        M = A @ A.T
        if np.linalg.cond(M) > 1e12:
            raise ValueError(f"view{view_index} orthographic matrix is ill-conditioned")
        x0 = A.T @ np.linalg.inv(M) @ b
        return x0, calib["d"]

    def _ortho_project_world_point_to_view(self, view_index: int, point_3d: np.array):
        """执行 `_ortho_project_world_point_to_view` 所对应的坐标系转换或投影运算。"""
        calib = self._get_active_orthographic_calibrations().get(view_index)
        if calib is None:
            raise ValueError(f"Missing orthographic calibration for view{view_index}")
        P = calib["P"]
        X = np.array([point_3d[0], point_3d[1], point_3d[2], 1.0], dtype=np.float64)
        uv_px = P @ X
        return self._pixel_to_slicer2d_raw(
            uv_px,
            calib["w"],
            calib["h"],
            calib["flip_x"],
            calib["flip_y"],
        )

    def _pixel_to_world_ray(self, view_index: int, pixel_2d: np.array):
        """执行 `_pixel_to_world_ray` 所对应的坐标系转换或投影运算。"""
        calib = self._get_active_perspective_calibrations().get(view_index)
        if calib is None:
            raise ValueError(f"缺少 view{view_index} 的透视标定参数")

        p_px = self._slicer2d_to_pixel_raw(
            pixel_2d,
            calib["w"],
            calib["h"],
            calib["flip_x"],
            calib["flip_y"],
        )
        return self.logic.pixelToWorldRay(
            p_px,
            calib["K"],
            calib["rvec"],
            calib["tvec"],
        )

    def _project_world_point_to_view(self, view_index: int, point_3d: np.array):
        """在透视模式下，使用视角标定参数将 3D 世界点投影为对应 2D 点。"""
        calib = self._get_active_perspective_calibrations().get(view_index)
        if calib is None:
            raise ValueError(f"缺少 view{view_index} 的透视标定参数")
        p_px = self.logic.projectPointToImage(
            point_3d,
            calib["K"],
            calib["dist"],
            calib["rvec"],
            calib["tvec"],
        )
        return self._pixel_to_slicer2d_raw(
            p_px,
            calib["w"],
            calib["h"],
            calib["flip_x"],
            calib["flip_y"],
        )

    def _ensureLinearTransformNodes(self) -> None:
        """确保 `_ensureLinearTransformNodes` 所需的节点或状态已准备就绪。"""
        transformNames = ["LinearTransform", "LinearTransform_1", "LinearTransform_2"]
        for name in transformNames:
            node = slicer.mrmlScene.GetFirstNodeByName(name)
            if node is None:
                node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLinearTransformNode", name)
        self._observe_transform_nodes_for_angles()

    def exit(self) -> None:
        """模块离开回调：断开参数节点与 GUI 的联结并移除修改监听。"""
        # Do not react to parameter node changes (GUI will be updated when the user enters into the module)
        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self._parameterNodeGuiTag = None
            self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)

    def onSceneStartClose(self, caller, event) -> None:
        """界面回调：执行 `onSceneStartClose` 对应的交互处理流程。"""
        # Parameter node will be reset, do not use it anymore
        self.setParameterNode(None)
        self._markersSorted = False
        self._reset_controlled_perturbation_run_state(clear_summary=True)
        self._update_controlled_perturbation_status()
        for node in self._angleObservedTransformNodes:
            try:
                self.removeObserver(node, vtk.vtkCommand.ModifiedEvent, self._on_marker_transform_modified)
            except Exception:
                pass
        self._angleObservedTransformNodes = []

    def onSceneEndClose(self, caller, event) -> None:
        """界面回调：执行 `onSceneEndClose` 对应的交互处理流程。"""
        # If this module is shown while the scene is closed then recreate a new parameter node immediately
        if self.parent.isEntered:
            self.initializeParameterNode()

    def initializeParameterNode(self) -> None:
        """初始化参数节点并在无输入时选中默认体数据。"""
        # Parameter node stores all user choices in parameter values, node selections, etc.
        # so that when the scene is saved and reloaded, these settings are restored.

        self.setParameterNode(self.logic.getParameterNode())

        # Select default input nodes if nothing is selected yet to save a few clicks for the user
        if not self._parameterNode.inputVolume:
            firstVolumeNode = slicer.mrmlScene.GetFirstNodeByClass("vtkMRMLScalarVolumeNode")
            if firstVolumeNode:
                self._parameterNode.inputVolume = firstVolumeNode

    def setParameterNode(self, inputParameterNode: Optional[BiplaneParameterNode]) -> None:
        """设置当前参数节点并建立 GUI 双向同步与修改观察。"""

        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)
        self._parameterNode = inputParameterNode
        if self._parameterNode:
            # Note: in the .ui file, a Qt dynamic property called "SlicerParameterName" is set on each
            # ui element that needs connection.
            self._parameterNodeGuiTag = self._parameterNode.connectGui(self.ui)
            self.addObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)
            self._checkCanApply()

    def _checkCanApply(self, caller=None, event=None) -> None:
        """根据输入体数据是否已选择，动态更新 shot 按钮可用状态与提示文案。"""
        canRun = bool(self._parameterNode and getattr(self._parameterNode, "inputVolume", None))
        self.ui.shot1AllButton.enabled = canRun
        self.ui.shot2AllButton.enabled = canRun
        self.ui.shot3AllButton.enabled = canRun
        self.ui.shot1AllButton.toolTip = _("Select input volume" if not canRun else "Capture shot")

    def flipImage(self, image):
        """执行图像镜像变换并保留几何信息。"""
        tmp = sitk.GetImageFromArray(sitk.GetArrayFromImage(image))
        flipFilter = sitk.FlipImageFilter()
        flipFilter.SetFlipAxes([True, True, False])
        imageMirror = flipFilter.Execute(tmp)
        imageMirror.CopyInformation(image)
        return imageMirror

    def sitk_image_to_vtk_image(self, sitk_image):
        # �?SimpleITK 图像转换�?NumPy 数组
        # sitk_image = self.flipImage(sitk_image)
        """将 SimpleITK 图像数据与空间信息转换为 vtkImageData，便于 Slicer/VTK 管线继续使用。"""
        np_array = sitk.GetArrayViewFromImage(sitk_image)

        # 获取图像的尺�?
        size = sitk_image.GetSize()

        # 创建一�?vtkImageData 对象
        vtk_image = vtk.vtkImageData()
        vtk_image.SetDimensions(size[0], size[1], 1)
        vtk_image.SetSpacing(sitk_image.GetSpacing()[0], sitk_image.GetSpacing()[1], 1)
        vtk_image.SetOrigin(sitk_image.GetOrigin()[0], sitk_image.GetOrigin()[1], 0)

        # �?NumPy 数组数据分配�?vtkImageData
        vtk_array = vtk.util.numpy_support.numpy_to_vtk(np_array.ravel(), deep=True, array_type=vtk.VTK_FLOAT)
        vtk_image.GetPointData().SetScalars(vtk_array)

        return vtk_image

    def vtk_image_to_sitk_image(self, vtk_image):
        # 获取vtkImageData的原始数�?
        """将 vtkImageData 像素数组还原为 SimpleITK 图像，用于后续 ITK 处理流程。"""
        nshape = tuple(reversed(vtk_image.GetDimensions()))
        vtk_data = vtk_image.GetPointData().GetScalars()
        # 将vtkImageData的原始数据转换为numpy数组
        numpy_array = vtk.util.numpy_support.vtk_to_numpy(vtk_data).reshape(nshape)

        # 将numpy数组转换为SimpleITK图像
        sitk_image = sitk.GetImageFromArray(numpy_array)
        # sitk_image = self.flipImage(sitk_image)
        sitk_image.SetSpacing([1, 1, 1])

        return sitk_image
    
    def numpy_to_vtk_image(self, array):
        """将 NumPy 数组封装为单层 vtkImageData，便于加载成体数据节点。"""
        img = vtk.vtkImageData()
        img.SetDimensions(array.shape[1], array.shape[0], 1)
        img.SetSpacing(1,1,1)
        img.SetOrigin(0,0,0)
        vtk_data = numpy_to_vtk(array.ravel(), array_type=vtk.VTK_FLOAT)
        img.GetPointData().SetScalars(vtk_data)
        return img

    def onShowVolumeButton(self):
        """界面回调：执行 `onShowVolumeButton` 对应的交互处理流程。"""
        bodyVolumeNode = self._getBodyVolumeNode()
        if not bodyVolumeNode:
            self._error("Input volume not found. Please select one in Input volume")
            return
        volRenLogic = slicer.modules.volumerendering.logic()
        displayNode = volRenLogic.CreateDefaultVolumeRenderingNodes(bodyVolumeNode)
        displayNode.SetVisibility(True)
        scalarRange = bodyVolumeNode.GetImageData().GetScalarRange()
        if scalarRange[1] - scalarRange[0] < 1500:
            # Small dynamic range, probably MRI
            displayNode.GetVolumePropertyNode().Copy(volRenLogic.GetPresetByName("MR-Default"))
        else:
            # Larger dynamic range, probably CT
            displayNode.GetVolumePropertyNode().Copy(volRenLogic.GetPresetByName("CT-Chest-Contrast-Enhanced"))

        try:
            slicer.util.resetThreeDViews()
        except Exception:
            pass

    def onShowMarkerButton(self):
        """界面回调：执行 `onShowMarkerButton` 对应的交互处理流程。"""
        markerModelNode = self._getMarkersModelNode()
        if markerModelNode is None:
            markerModelNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode")
            markerModelNode.SetName("markers")

        markerModelNode.SetAndObservePolyData(self.markerSource)

        displayNode = markerModelNode.GetDisplayNode()
        if displayNode is None:
            displayNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelDisplayNode")
            markerModelNode.SetAndObserveDisplayNodeID(displayNode.GetID())
        displayNode.SetColor(0, 0, 0)
        displayNode.SetVisibility(True)
        if hasattr(displayNode, "SetVisibility3D"):
            displayNode.SetVisibility3D(True)
        markerModelNode.SetDisplayVisibility(True)

    def onShowTestPointButton(self):
        """界面回调：执行 `onShowTestPointButton` 对应的交互处理流程。"""
        markerModelNode = self._getMarkersModelNode()
        if not markerModelNode:
            self._error("需要先点击 showMarker 生成 markers")
            return

        polyData = markerModelNode.GetPolyData()
        if polyData is None or polyData.GetNumberOfPoints() == 0:
            self._error("markers has no valid point data")
            return

        center = [0.0, 0.0, 0.0]
        polyData.GetCenter(center)
        self._set_testpoint_position(center)

    def onShowKnifeButton(self):
        """界面回调：执行 `onShowKnifeButton` 对应的交互处理流程。"""
        volumeNode = self._getBodyVolumeNode()
        if not volumeNode:
            self._error("Input volume not found. Please select one in Input volume")
            return
        center = self._get_volume_center(volumeNode)
        if center is None:
            self._error("Failed to get volume center")
            return

        knifeNode = slicer.mrmlScene.GetFirstNodeByName("knife")
        if knifeNode is None:
            knifeNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", "knife")
            knifeNode.CreateDefaultDisplayNodes()

        if knifeNode.GetNumberOfControlPoints() < 1:
            knifeNode.AddControlPoint(center)
        else:
            knifeNode.SetNthControlPointPosition(0, center)

        displayNode = knifeNode.GetDisplayNode()
        if displayNode:
            displayNode.SetVisibility(True)
            displayNode.SetViewNodeIDs(["vtkMRMLViewNode1"])
            displayNode.SetVisibility3D(True)
            if hasattr(displayNode, "SetVisibility2D"):
                displayNode.SetVisibility2D(False)
            displayNode.SetPointLabelsVisibility(False)
            displayNode.SetGlyphScale(3)
            displayNode.SetSelectedColor(1.0, 0.93, 0.2)
            displayNode.SetColor(1.0, 0.93, 0.2)

        self._set_selector_current_node(self.ui.knifeSelector, knifeNode)

    def onCopyMarkerPoint(self):
        """界面回调：执行 `onCopyMarkerPoint` 对应的交互处理流程。"""
        sourceNode = self.ui.copySourceSelector.currentNode()
        targetNode = self.ui.copyTargetSelector.currentNode()
        if not sourceNode:
            self._error("Please select a source marker node")
            return
        if not targetNode:
            self._error("Please select a target marker node")
            return
        if sourceNode.GetNumberOfControlPoints() < 1:
            self._error("Source marker has no control points")
            return

        index = 0
        sourcePos = sourceNode.GetNthControlPointPosition(index)

        if targetNode.GetNumberOfControlPoints() <= index:
            targetNode.AddControlPoint(sourcePos)
        else:
            targetNode.SetNthControlPointPosition(index, sourcePos)

    def _copy_center_to_point(self, source_name: str, target_name: str, view_node_id: str, color):
        """将源节点首个控制点复制到目标节点，并按视图设定显示颜色与可见性。"""
        source_node = slicer.mrmlScene.GetFirstNodeByName(source_name)
        if source_node is None or source_node.GetNumberOfControlPoints() < 1:
            self._error(f"Control point missing in {source_name}")
            return

        target_node = slicer.mrmlScene.GetFirstNodeByName(target_name)
        if target_node is None:
            target_node = slicer.vtkMRMLMarkupsFiducialNode()
            target_node.SetName(target_name)
            slicer.mrmlScene.AddNode(target_node)
            target_node.CreateDefaultDisplayNodes()

        source_pos = source_node.GetNthControlPointPosition(0)
        if target_node.GetNumberOfControlPoints() < 1:
            target_node.AddControlPoint(source_pos)
        else:
            target_node.SetNthControlPointPosition(0, source_pos)

        display_node = target_node.GetDisplayNode()
        if display_node:
            display_node.SetVisibility(True)
            display_node.SetViewNodeIDs([view_node_id])
            display_node.SetVisibility3D(False)
            display_node.SetPointLabelsVisibility(False)
            display_node.SetSelectedColor(color)
            display_node.SetColor(color)

    def onCopyBlackCenter1(self):
        """界面回调：执行 `onCopyBlackCenter1` 对应的交互处理流程。"""
        self._copy_center_to_point("blackCenter1", "PointRed", "vtkMRMLSliceNodeRed", (1.0, 0.0, 0.0))

    def onCopyBlackCenter2(self):
        """界面回调：执行 `onCopyBlackCenter2` 对应的交互处理流程。"""
        self._copy_center_to_point("blackCenter2", "PointGreen", "vtkMRMLSliceNodeGreen", (0.0, 1.0, 0.0))

    def onShot1Button(self):
        """界面回调：执行 `onShot1Button` 对应的交互处理流程。"""
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("Please load a volume and click showMarker first")
            return

        bodyVolumeNode.SetDisplayVisibility(True)
        markerModelNode.SetDisplayVisibility(False)

        testPointNode = slicer.mrmlScene.GetFirstNodeByName("testPoint")
        testPointWasVisible = False
        if testPointNode:
            tpDisplay = testPointNode.GetDisplayNode()
            if tpDisplay:
                testPointWasVisible = bool(tpDisplay.GetVisibility())
                tpDisplay.SetVisibility(False)

        saveBodyFile = os.path.join(self.savePath, "shot1Body.png")
        visibility_backup = self._limit_display_nodes_for_shot(
            [bodyVolumeNode, markerModelNode, testPointNode]
        )
        try:
            self._captureViewToFile(saveBodyFile)
        finally:
            self._restore_display_nodes(visibility_backup)

        markerModelNode.SetDisplayVisibility(True)
        markerDisplayNode = markerModelNode.GetDisplayNode()
        if markerDisplayNode:
            if hasattr(markerDisplayNode, "SetVisibility3D"):
                markerDisplayNode.SetVisibility3D(True)
            markerDisplayNode.SetVisibility(True)

        if testPointNode and testPointWasVisible:
            tpDisplay = testPointNode.GetDisplayNode()
            if tpDisplay:
                tpDisplay.SetVisibility(True)


    def onShot1AllButton(self):
        """Timed wrapper for the full shot1 acquisition workflow."""
        return self._run_timed_step("shot1_all_ms", self._onShot1AllButton_impl)

    def _onShot1AllButton_impl(self):
        """界面回调：执行 `onShot1AllButton` 对应的交互处理流程。"""
        self.onShot1Button()
        self.onShot1ButtonAgain()
        self.onShot1ButtonShow()
        self.onShowVolumeButton()
        self._ensure_center_fiducial("PointRed", "vtkMRMLSliceNodeRed", (1.0, 0.0, 0.0))
        point_red = slicer.mrmlScene.GetFirstNodeByName("PointRed")
        self._set_selector_current_node(self.ui.Red2DPSelector, point_red)


    def onShot1ButtonAgain(self):
        """界面回调：执行 `onShot1ButtonAgain` 对应的交互处理流程。"""
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("Please load a volume and click showMarker first")
            return

        bodyVolumeNode.SetDisplayVisibility(False)
        displayNodes = slicer.mrmlScene.GetNodesByClass("vtkMRMLDisplayNode")
        displayNodes.InitTraversal()
        displayNode = displayNodes.GetNextItemAsObject()

        visibilityBackup = []
        while displayNode:
            if hasattr(displayNode, "GetVisibility3D"):
                visibilityBackup.append(
                    (displayNode, displayNode.GetVisibility(), displayNode.GetVisibility3D())
                )
                displayNode.SetVisibility3D(False)
            else:
                visibilityBackup.append((displayNode, displayNode.GetVisibility(), None))
                displayNode.SetVisibility(False)
            displayNode = displayNodes.GetNextItemAsObject()

        markerModelNode.SetDisplayVisibility(True)
        markerDisplayNode = markerModelNode.GetDisplayNode()
        if markerDisplayNode:
            if hasattr(markerDisplayNode, "SetVisibility3D"):
                markerDisplayNode.SetVisibility3D(True)
            markerDisplayNode.SetVisibility(True)

        testPointNode = slicer.mrmlScene.GetFirstNodeByName("testPoint")
        tpDisplay = None
        if testPointNode:
            tpDisplay = testPointNode.GetDisplayNode()
            if tpDisplay:
                if hasattr(tpDisplay, "SetVisibility3D"):
                    tpDisplay.SetVisibility3D(False)
                tpDisplay.SetVisibility(False)

        saveMarkerFile = os.path.join(self.savePath, "shot1Markers.png")
        saveTestPointFile = os.path.join(self.savePath, "shot1TestPoint.png")
        try:
            self._captureViewToFile(saveMarkerFile)

            if markerDisplayNode:
                if hasattr(markerDisplayNode, "SetVisibility3D"):
                    markerDisplayNode.SetVisibility3D(False)
                markerDisplayNode.SetVisibility(False)

            if tpDisplay:
                if hasattr(tpDisplay, "SetVisibility3D"):
                    tpDisplay.SetVisibility3D(True)
                tpDisplay.SetVisibility(True)

            self._captureViewToFile(saveTestPointFile)
        finally:
            for displayNode, visibility, visibility3D in visibilityBackup:
                displayNode.SetVisibility(visibility)
                if visibility3D is not None and hasattr(displayNode, "SetVisibility3D"):
                    displayNode.SetVisibility3D(visibility3D)


    def onShot1ButtonShow(self):
        """界面回调：执行 `onShot1ButtonShow` 对应的交互处理流程。"""
        saveBodyFile = os.path.join(self.savePath, "shot1Body.png")
        saveMarkerFile = os.path.join(self.savePath, "shot1Markers.png")
        saveTestPointFile = os.path.join(self.savePath, "shot1TestPoint.png")
        saveNiiFile = os.path.join(self.savePath, "shot1.nii.gz")

        imgBody = self._requireImage(saveBodyFile, "shot1Body")
        imgMarkers = self._requireImage(saveMarkerFile, "shot1Markers")
        imgTestPoint = self._requireImage(saveTestPointFile, "shot1TestPoint")
        if imgBody is None or imgMarkers is None or imgTestPoint is None:
            return
        imgBodyGray = cv2.cvtColor(imgBody, cv2.COLOR_BGR2GRAY)
        imgMarkersGray = cv2.cvtColor(imgMarkers, cv2.COLOR_BGR2GRAY)
        imgTestPointGray = cv2.cvtColor(imgTestPoint, cv2.COLOR_BGR2GRAY)
        imgBodyGrayArr = np.array(imgBodyGray)
        imgMarkersGrayArr = np.array(imgMarkersGray)
        imgTestPointGrayArr = np.array(imgTestPointGray)
        imgMarkersGrayArr = 1000 * ((imgMarkersGrayArr / 255) - 1)  # [-1000, 0]
        imgMarkersGrayArrTMP = (imgMarkersGrayArr + 1000) / 1000
        imgTestPointArr = 100 * ((imgTestPointGrayArr / 255) - 1)  # [-100, 0]
        imgTestPointMask = (imgTestPointArr + 100) / 100

        imgArr = imgBodyGrayArr * imgMarkersGrayArrTMP + imgMarkersGrayArr
        imgArr = imgArr * imgTestPointMask + imgTestPointArr

        imgITK = sitk.GetImageFromArray(imgArr)
        vtkImage = self.sitk_image_to_vtk_image(imgITK)
        
        writer = vtk.vtkNIFTIImageWriter()
        writer.SetInputData(vtkImage)
        writer.SetFileName(saveNiiFile)
        writer.Update()
        writer.Write()

        volumeNode = slicer.mrmlScene.GetFirstNodeByName("shot1")
        if volumeNode is None:
            volumeNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode")
            volumeNode.SetName("shot1")
        volumeNode.SetAndObserveImageData(vtkImage)

        mm = [[-1,0,0],[0,-1,0],[0,0,1]]
        volumeNode.SetIJKToRASDirections(mm)

        sliceNode = slicer.mrmlScene.GetNodeByID("vtkMRMLSliceNodeRed")
        sliceNode.SetOrientationToAxial()
        appLogic = slicer.app.applicationLogic()
        sliceLogic = appLogic.GetSliceLogic(sliceNode)
        sliceLogic.GetSliceCompositeNode().SetBackgroundVolumeID(volumeNode.GetID())

        layoutManager = slicer.app.layoutManager()
        sliceWidget = layoutManager.sliceWidget(sliceNode.GetLayoutName())
        sliceWidget.sliceController().fitSliceToBackground()

    def onShot2Button(self):
        """界面回调：执行 `onShot2Button` 对应的交互处理流程。"""
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("Please load a volume and click showMarker first")
            return

        bodyVolumeNode.SetDisplayVisibility(True)
        markerModelNode.SetDisplayVisibility(False)

        testPointNode = slicer.mrmlScene.GetFirstNodeByName("testPoint")
        testPointWasVisible = False
        if testPointNode:
            tpDisplay = testPointNode.GetDisplayNode()
            if tpDisplay:
                testPointWasVisible = bool(tpDisplay.GetVisibility())
                tpDisplay.SetVisibility(False)

        saveBodyFile = os.path.join(self.savePath, "shot2Body.png")
        visibility_backup = self._limit_display_nodes_for_shot(
            [bodyVolumeNode, markerModelNode, testPointNode]
        )
        try:
            self._captureViewToFile(saveBodyFile)
        finally:
            self._restore_display_nodes(visibility_backup)

        markerModelNode.SetDisplayVisibility(True)
        markerDisplayNode = markerModelNode.GetDisplayNode()
        if markerDisplayNode:
            if hasattr(markerDisplayNode, "SetVisibility3D"):
                markerDisplayNode.SetVisibility3D(True)
            markerDisplayNode.SetVisibility(True)

        if testPointNode and testPointWasVisible:
            tpDisplay = testPointNode.GetDisplayNode()
            if tpDisplay:
                tpDisplay.SetVisibility(True)

    
    def onShot2AllButton(self):
        """Timed wrapper for the full shot2 acquisition workflow."""
        return self._run_timed_step("shot2_all_ms", self._onShot2AllButton_impl)

    def _onShot2AllButton_impl(self):
        """界面回调：执行 `onShot2AllButton` 对应的交互处理流程。"""
        self.onShot2Button()
        self.onShot2ButtonAgain()
        self.onShot2ButtonShow()
        self.onShowVolumeButton()
        self._ensure_center_fiducial("PointGreen", "vtkMRMLSliceNodeGreen", (0.0, 1.0, 0.0))
        point_green = slicer.mrmlScene.GetFirstNodeByName("PointGreen")
        self._set_selector_current_node(self.ui.Green2DPSelector, point_green)
        self._update_shot2_angle_display()


    def onShot2ButtonAgain(self):
        """界面回调：执行 `onShot2ButtonAgain` 对应的交互处理流程。"""
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("Please load a volume and click showMarker first")
            return

        bodyVolumeNode.SetDisplayVisibility(False)
        displayNodes = slicer.mrmlScene.GetNodesByClass("vtkMRMLDisplayNode")
        displayNodes.InitTraversal()
        displayNode = displayNodes.GetNextItemAsObject()

        visibilityBackup = []
        while displayNode:
            if hasattr(displayNode, "GetVisibility3D"):
                visibilityBackup.append(
                    (displayNode, displayNode.GetVisibility(), displayNode.GetVisibility3D())
                )
                displayNode.SetVisibility3D(False)
            else:
                visibilityBackup.append((displayNode, displayNode.GetVisibility(), None))
                displayNode.SetVisibility(False)
            displayNode = displayNodes.GetNextItemAsObject()

        markerModelNode.SetDisplayVisibility(True)
        markerDisplayNode = markerModelNode.GetDisplayNode()
        if markerDisplayNode:
            if hasattr(markerDisplayNode, "SetVisibility3D"):
                markerDisplayNode.SetVisibility3D(True)
            markerDisplayNode.SetVisibility(True)

        testPointNode = slicer.mrmlScene.GetFirstNodeByName("testPoint")
        tpDisplay = None
        if testPointNode:
            tpDisplay = testPointNode.GetDisplayNode()
            if tpDisplay:
                if hasattr(tpDisplay, "SetVisibility3D"):
                    tpDisplay.SetVisibility3D(False)
                tpDisplay.SetVisibility(False)

        saveMarkerFile = os.path.join(self.savePath, "shot2Markers.png")
        saveTestPointFile = os.path.join(self.savePath, "shot2TestPoint.png")
        try:
            self._captureViewToFile(saveMarkerFile)

            if markerDisplayNode:
                if hasattr(markerDisplayNode, "SetVisibility3D"):
                    markerDisplayNode.SetVisibility3D(False)
                markerDisplayNode.SetVisibility(False)

            if tpDisplay:
                if hasattr(tpDisplay, "SetVisibility3D"):
                    tpDisplay.SetVisibility3D(True)
                tpDisplay.SetVisibility(True)

            self._captureViewToFile(saveTestPointFile)
        finally:
            for displayNode, visibility, visibility3D in visibilityBackup:
                displayNode.SetVisibility(visibility)
                if visibility3D is not None and hasattr(displayNode, "SetVisibility3D"):
                    displayNode.SetVisibility3D(visibility3D)


    def onShot2ButtonShow(self):
        """界面回调：执行 `onShot2ButtonShow` 对应的交互处理流程。"""
        saveBodyFile = os.path.join(self.savePath, "shot2Body.png")
        saveMarkerFile = os.path.join(self.savePath, "shot2Markers.png")
        saveTestPointFile = os.path.join(self.savePath, "shot2TestPoint.png")
        saveNiiFile = os.path.join(self.savePath, "shot2.nii.gz")

        imgBody = self._requireImage(saveBodyFile, "shot2Body")
        imgMarkers = self._requireImage(saveMarkerFile, "shot2Markers")
        imgTestPoint = self._requireImage(saveTestPointFile, "shot2TestPoint")
        if imgBody is None or imgMarkers is None or imgTestPoint is None:
            return
        imgBodyGray = cv2.cvtColor(imgBody, cv2.COLOR_BGR2GRAY)
        imgMarkersGray = cv2.cvtColor(imgMarkers, cv2.COLOR_BGR2GRAY)
        imgTestPointGray = cv2.cvtColor(imgTestPoint, cv2.COLOR_BGR2GRAY)
        imgBodyGrayArr = np.array(imgBodyGray)
        imgMarkersGrayArr = np.array(imgMarkersGray)
        imgTestPointGrayArr = np.array(imgTestPointGray)
        imgMarkersGrayArr = 1000 * ((imgMarkersGrayArr / 255) - 1)  # [-1000, 0]
        imgMarkersGrayArrTMP = (imgMarkersGrayArr + 1000) / 1000
        imgTestPointArr = 100 * ((imgTestPointGrayArr / 255) - 1)  # [-100, 0]
        imgTestPointMask = (imgTestPointArr + 100) / 100

        imgArr = imgBodyGrayArr * imgMarkersGrayArrTMP + imgMarkersGrayArr
        imgArr = imgArr * imgTestPointMask + imgTestPointArr

        imgITK = sitk.GetImageFromArray(imgArr)
        vtkImage = self.sitk_image_to_vtk_image(imgITK)

        writer = vtk.vtkNIFTIImageWriter()
        writer.SetInputData(vtkImage)
        writer.SetFileName(saveNiiFile)
        writer.Update()
        writer.Write()

        volumeNode = slicer.mrmlScene.GetFirstNodeByName("shot2")
        if volumeNode is None:
            volumeNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode")
            volumeNode.SetName("shot2")
        volumeNode.SetAndObserveImageData(vtkImage)

        mm = [[-1,0,0],[0,-1,0],[0,0,1]]
        volumeNode.SetIJKToRASDirections(mm)

        sliceNode = slicer.mrmlScene.GetNodeByID("vtkMRMLSliceNodeGreen")
        sliceNode.SetOrientationToAxial()
        appLogic = slicer.app.applicationLogic()
        sliceLogic = appLogic.GetSliceLogic(sliceNode)
        sliceLogic.GetSliceCompositeNode().SetBackgroundVolumeID(volumeNode.GetID())

        layoutManager = slicer.app.layoutManager()
        sliceWidget = layoutManager.sliceWidget(sliceNode.GetLayoutName())
        sliceWidget.sliceController().fitSliceToBackground()

    def onShot3Button(self):
        """界面回调：执行 `onShot3Button` 对应的交互处理流程。"""
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("Please load a volume and click showMarker first")
            return

        bodyVolumeNode.SetDisplayVisibility(True)
        markerModelNode.SetDisplayVisibility(False)

        testPointNode = slicer.mrmlScene.GetFirstNodeByName("testPoint")
        testPointWasVisible = False
        if testPointNode:
            tpDisplay = testPointNode.GetDisplayNode()
            if tpDisplay:
                testPointWasVisible = bool(tpDisplay.GetVisibility())
                tpDisplay.SetVisibility(False)

        saveBodyFile = os.path.join(self.savePath, "shot3Body.png")
        visibility_backup = self._limit_display_nodes_for_shot(
            [bodyVolumeNode, markerModelNode, testPointNode]
        )
        try:
            self._captureViewToFile(saveBodyFile)
        finally:
            self._restore_display_nodes(visibility_backup)

        markerModelNode.SetDisplayVisibility(True)
        markerDisplayNode = markerModelNode.GetDisplayNode()
        if markerDisplayNode:
            if hasattr(markerDisplayNode, "SetVisibility3D"):
                markerDisplayNode.SetVisibility3D(True)
            markerDisplayNode.SetVisibility(True)

        if testPointNode and testPointWasVisible:
            tpDisplay = testPointNode.GetDisplayNode()
            if tpDisplay:
                tpDisplay.SetVisibility(True)

    def onShot3AllButton(self):
        """Timed wrapper for the full shot3 acquisition workflow."""
        return self._run_timed_step("shot3_all_ms", self._onShot3AllButton_impl)

    def _onShot3AllButton_impl(self):
        """界面回调：执行 `onShot3AllButton` 对应的交互处理流程。"""
        self.onShot3Button()
        self.onShot3ButtonAgain()
        self.onShot3ButtonShow()
        self.onShowVolumeButton()
        self._update_shot3_angle_display()

    def onShot3ButtonAgain(self):
        """界面回调：执行 `onShot3ButtonAgain` 对应的交互处理流程。"""
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("Please load a volume and click showMarker first")
            return

        bodyVolumeNode.SetDisplayVisibility(False)
        displayNodes = slicer.mrmlScene.GetNodesByClass("vtkMRMLDisplayNode")
        displayNodes.InitTraversal()
        displayNode = displayNodes.GetNextItemAsObject()

        visibilityBackup = []
        while displayNode:
            if hasattr(displayNode, "GetVisibility3D"):
                visibilityBackup.append(
                    (displayNode, displayNode.GetVisibility(), displayNode.GetVisibility3D())
                )
                displayNode.SetVisibility3D(False)
            else:
                visibilityBackup.append((displayNode, displayNode.GetVisibility(), None))
                displayNode.SetVisibility(False)
            displayNode = displayNodes.GetNextItemAsObject()

        markerModelNode.SetDisplayVisibility(True)
        markerDisplayNode = markerModelNode.GetDisplayNode()
        if markerDisplayNode:
            if hasattr(markerDisplayNode, "SetVisibility3D"):
                markerDisplayNode.SetVisibility3D(True)
            markerDisplayNode.SetVisibility(True)

        testPointNode = slicer.mrmlScene.GetFirstNodeByName("testPoint")
        tpDisplay = None
        if testPointNode:
            tpDisplay = testPointNode.GetDisplayNode()
            if tpDisplay:
                if hasattr(tpDisplay, "SetVisibility3D"):
                    tpDisplay.SetVisibility3D(False)
                tpDisplay.SetVisibility(False)

        saveMarkerFile = os.path.join(self.savePath, "shot3Markers.png")
        saveTestPointFile = os.path.join(self.savePath, "shot3TestPoint.png")
        try:
            self._captureViewToFile(saveMarkerFile)

            if markerDisplayNode:
                if hasattr(markerDisplayNode, "SetVisibility3D"):
                    markerDisplayNode.SetVisibility3D(False)
                markerDisplayNode.SetVisibility(False)

            if tpDisplay:
                if hasattr(tpDisplay, "SetVisibility3D"):
                    tpDisplay.SetVisibility3D(True)
                tpDisplay.SetVisibility(True)

            self._captureViewToFile(saveTestPointFile)
        finally:
            for displayNode, visibility, visibility3D in visibilityBackup:
                displayNode.SetVisibility(visibility)
                if visibility3D is not None and hasattr(displayNode, "SetVisibility3D"):
                    displayNode.SetVisibility3D(visibility3D)


    def onShot3ButtonShow(self):
        """界面回调：执行 `onShot3ButtonShow` 对应的交互处理流程。"""
        saveBodyFile = os.path.join(self.savePath, "shot3Body.png")
        saveMarkerFile = os.path.join(self.savePath, "shot3Markers.png")
        saveTestPointFile = os.path.join(self.savePath, "shot3TestPoint.png")
        saveNiiFile = os.path.join(self.savePath, "shot3.nii.gz")

        imgBody = self._requireImage(saveBodyFile, "shot3Body")
        imgMarkers = self._requireImage(saveMarkerFile, "shot3Markers")
        imgTestPoint = self._requireImage(saveTestPointFile, "shot3TestPoint")
        if imgBody is None or imgMarkers is None or imgTestPoint is None:
            return
        imgBodyGray = cv2.cvtColor(imgBody, cv2.COLOR_BGR2GRAY)
        imgMarkersGray = cv2.cvtColor(imgMarkers, cv2.COLOR_BGR2GRAY)
        imgTestPointGray = cv2.cvtColor(imgTestPoint, cv2.COLOR_BGR2GRAY)
        imgBodyGrayArr = np.array(imgBodyGray)
        imgMarkersGrayArr = np.array(imgMarkersGray)
        imgTestPointGrayArr = np.array(imgTestPointGray)
        imgMarkersGrayArr = 1000 * ((imgMarkersGrayArr / 255) - 1)  # [-1000, 0]
        imgMarkersGrayArrTMP = (imgMarkersGrayArr + 1000) / 1000
        imgTestPointArr = 100 * ((imgTestPointGrayArr / 255) - 1)  # [-100, 0]
        imgTestPointMask = (imgTestPointArr + 100) / 100

        imgArr = imgBodyGrayArr * imgMarkersGrayArrTMP + imgMarkersGrayArr
        imgArr = imgArr * imgTestPointMask + imgTestPointArr

        imgITK = sitk.GetImageFromArray(imgArr)
        vtkImage = self.sitk_image_to_vtk_image(imgITK)

        writer = vtk.vtkNIFTIImageWriter()
        writer.SetInputData(vtkImage)
        writer.SetFileName(saveNiiFile)
        writer.Update()
        writer.Write()

        volumeNode = slicer.mrmlScene.GetFirstNodeByName("shot3")
        if volumeNode is None:
            volumeNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode")
            volumeNode.SetName("shot3")
        volumeNode.SetAndObserveImageData(vtkImage)

        mm = [[-1,0,0],[0,-1,0],[0,0,1]]
        volumeNode.SetIJKToRASDirections(mm)

        sliceNode = slicer.mrmlScene.GetNodeByID("vtkMRMLSliceNodeYellow")
        sliceNode.SetOrientationToAxial()
        appLogic = slicer.app.applicationLogic()
        sliceLogic = appLogic.GetSliceLogic(sliceNode)
        sliceLogic.GetSliceCompositeNode().SetBackgroundVolumeID(volumeNode.GetID())

        layoutManager = slicer.app.layoutManager()
        sliceWidget = layoutManager.sliceWidget(sliceNode.GetLayoutName())
        sliceWidget.sliceController().fitSliceToBackground()

    def onBlackCenterButton(self):
        """Timed wrapper for black-center extraction."""
        return self._run_timed_step("black_center_ms", self._onBlackCenterButton_impl)

    def _onBlackCenterButton_impl(self):
        """界面回调：执行 `onBlackCenterButton` 对应的交互处理流程。"""
        volumeShot1Node = slicer.mrmlScene.GetFirstNodeByName("shot1")
        volumeShot2Node = slicer.mrmlScene.GetFirstNodeByName("shot2")
        volumeShot3Node = slicer.mrmlScene.GetFirstNodeByName("shot3")
        if not volumeShot1Node or not volumeShot2Node or not volumeShot3Node:
            self._error("需要先生成 shot1/shot2/shot3 三个切片图像")
            return

        pos1 = self._get_black_center_from_volume(volumeShot1Node)
        pos2 = self._get_black_center_from_volume(volumeShot2Node)
        pos3 = self._get_black_center_from_volume(volumeShot3Node)

        if pos1 is None or pos2 is None or pos3 is None:
            self._error("testPoint pixel center not found. Please verify pixel value -100 exists in image")
            return

        self._show_black_center_marker("blackCenter1", "vtkMRMLSliceNodeRed", pos1)
        self._show_black_center_marker("blackCenter2", "vtkMRMLSliceNodeGreen", pos2)
        self._show_black_center_marker("blackCenter3", "vtkMRMLSliceNodeYellow", pos3)

    def onMarkersSortButton(self):
        """Timed wrapper for marker sorting and calibration initialization."""
        return self._run_timed_step("markers_sort_ms", self._onMarkersSortButton_impl)

    def _onMarkersSortButton_impl(self):
        """对 shot1/2/3 执行 marker 提取与编号排序，同步生成显示节点并触发标定初始化。"""
        self._reset_controlled_perturbation_run_state(clear_summary=True)
        self._update_controlled_perturbation_status()
        self.markerSortMetrics = {}
        volumeShot1Node = slicer.mrmlScene.GetFirstNodeByName("shot1")
        volumeShot2Node = slicer.mrmlScene.GetFirstNodeByName("shot2")
        volumeShot3Node = slicer.mrmlScene.GetFirstNodeByName("shot3")
        if not volumeShot1Node or not volumeShot2Node or not volumeShot3Node:
            self._error("需要先生成 shot1/shot2/shot3 三个切片图像")
            return

        shot1vtkImage = volumeShot1Node.GetImageData()
        shot2vtkImage = volumeShot2Node.GetImageData()
        shot3vtkImage = volumeShot3Node.GetImageData()

        shot1ImageITK = self.vtk_image_to_sitk_image(shot1vtkImage)
        shot2ImageITK = self.vtk_image_to_sitk_image(shot2vtkImage)
        shot3ImageITK = self.vtk_image_to_sitk_image(shot3vtkImage)

        markerSortLogic1 = GenerateMarkers()
        markerSortLogic2 = GenerateMarkers()
        markerSortLogic3 = GenerateMarkers()

        try:
            markerSortLogic1.getMarkerCenters(shot1ImageITK)
            self.bigMarkersSort1, self.smallMarkersSort1 = self._sort_detected_markers_for_view(1, markerSortLogic1)

            markerSortLogic2.getMarkerCenters(shot2ImageITK)
            self.bigMarkersSort2, self.smallMarkersSort2 = self._sort_detected_markers_for_view(2, markerSortLogic2)

            markerSortLogic3.getMarkerCenters(shot3ImageITK)
            self.bigMarkersSort3, self.smallMarkersSort3 = self._sort_detected_markers_for_view(3, markerSortLogic3)
        except Exception as e:
            self._markersSorted = False
            self._error("Marker detection/sorting failed", detailedText=str(e))
            return

        # 转换�?slicer 坐标�?
        self.bigMarkersSort1 = markerSortLogic1.move2slicer(self.bigMarkersSort1)
        self.smallMarkersSort1 = markerSortLogic1.move2slicer(self.smallMarkersSort1)

        self.bigMarkersSort2 = markerSortLogic2.move2slicer(self.bigMarkersSort2)
        self.smallMarkersSort2 = markerSortLogic2.move2slicer(self.smallMarkersSort2)

        self.bigMarkersSort3 = markerSortLogic3.move2slicer(self.bigMarkersSort3)
        self.smallMarkersSort3 = markerSortLogic3.move2slicer(self.smallMarkersSort3)

        def update_markups_node(node_name, view_node_id, color, big_markers, small_markers):
            markups_node = slicer.mrmlScene.GetFirstNodeByName(node_name)
            if markups_node is None:
                markups_node = slicer.vtkMRMLMarkupsFiducialNode()
                markups_node.SetName(node_name)
                slicer.mrmlScene.AddNode(markups_node)

            display_node = markups_node.GetDisplayNode()
            if display_node is None:
                markups_node.CreateDefaultDisplayNodes()
                display_node = markups_node.GetDisplayNode()

            if display_node:
                display_node.SetVisibility(True)
                display_node.SetViewNodeIDs([view_node_id])
                display_node.SetVisibility3D(False)
                display_node.SetGlyphScale(0.15)
                display_node.SetSelectedColor(color)

            markups_node.RemoveAllControlPoints()
            for point, label in list(big_markers.items()) + list(small_markers.items()):
                index = markups_node.AddControlPoint(point)
                markups_node.SetNthControlPointLabel(index, str(label))

            return markups_node

        update_markups_node(
            "markers1",
            "vtkMRMLSliceNodeRed",
            [1, 0, 0],
            self.bigMarkersSort1,
            self.smallMarkersSort1,
        )
        update_markups_node(
            "markers2",
            "vtkMRMLSliceNodeGreen",
            [0, 1, 0],
            self.bigMarkersSort2,
            self.smallMarkersSort2,
        )
        update_markups_node(
            "markers3",
            "vtkMRMLSliceNodeYellow",
            [0, 0, 1],
            self.bigMarkersSort3,
            self.smallMarkersSort3,
        )

        # 编排所有marker顺序
        ok = self.initMarkers()
        if not ok:
            self._markersSorted = False
            return
        # 并且计算 3 个平行光向量
        self.initLightVec()
        self._markersSorted = True

    def initMarkers(self):
        """Timed wrapper for marker initialization and calibration refresh."""
        return self._run_timed_step("init_markers_ms", self._initMarkers_impl)

    def _initMarkers_impl(self):
        """根据三视角 marker 匹配结果构建 2D/3D 映射矩阵、刚体变换和投影标定数据。"""
        bigMarker3DDic = self.generateMarkers.bigMarker3DDic
        smallMarker3DDic = self.generateMarkers.smallMarker3DDic

        linearTransformNode1 = slicer.mrmlScene.GetFirstNodeByName("LinearTransform")
        linearTransformNode2 = slicer.mrmlScene.GetFirstNodeByName("LinearTransform_1")
        linearTransformNode3 = slicer.mrmlScene.GetFirstNodeByName("LinearTransform_2")

        self.bigMarker3DDic1 = self.generateMarkers.getMarkerTransform(linearTransformNode1, bigMarker3DDic)
        self.smallMarker3DDic1 = self.generateMarkers.getMarkerTransform(linearTransformNode1, smallMarker3DDic)

        self.bigMarker3DDic2 = self.generateMarkers.getMarkerTransform(linearTransformNode2, bigMarker3DDic)
        self.smallMarker3DDic2 = self.generateMarkers.getMarkerTransform(linearTransformNode2, smallMarker3DDic)

        self.bigMarker3DDic3 = self.generateMarkers.getMarkerTransform(linearTransformNode3, bigMarker3DDic)
        self.smallMarker3DDic3 = self.generateMarkers.getMarkerTransform(linearTransformNode3, smallMarker3DDic)

        # 调式显示点，判断2D图像上的点是否正�?
        def update_markups_3d(node_name, color, big_markers, small_markers):
            markups_node = slicer.mrmlScene.GetFirstNodeByName(node_name)
            if markups_node is None:
                markups_node = slicer.vtkMRMLMarkupsFiducialNode()
                markups_node.SetName(node_name)
                slicer.mrmlScene.AddNode(markups_node)

            display_node = markups_node.GetDisplayNode()
            if display_node is None:
                markups_node.CreateDefaultDisplayNodes()
                display_node = markups_node.GetDisplayNode()

            if display_node:
                display_node.SetVisibility(False)
                display_node.SetVisibility3D(True)
                display_node.SetGlyphScale(0.15)
                display_node.SetSelectedColor(color)

            markups_node.RemoveAllControlPoints()
            for key, point in list(big_markers.items()) + list(small_markers.items()):
                index = markups_node.AddControlPoint(point)
                markups_node.SetNthControlPointLabel(index, str(key))

            return markups_node

        update_markups_3d("markers3D1", [1, 0, 0], self.bigMarker3DDic1, self.smallMarker3DDic1)
        update_markups_3d("markers3D2", [0, 1, 0], self.bigMarker3DDic2, self.smallMarker3DDic2)
        update_markups_3d("markers3D3", [0, 0, 1], self.bigMarker3DDic3, self.smallMarker3DDic3)

        # 计算所有投影变换矩阵与刚体变换矩阵
        
        # 1, 先计�?3D �?2D 的变�?

        # 使用出厂设置�?marker 坐标
        # -----------------------------------------------------------------
        originBigMarker3DDic = GenerateMarkers().bigMarker3DDic
        originSmallMarker3DDic = GenerateMarkers().smallMarker3DDic

        self.originBigMarker3D_Z = originBigMarker3DDic[1][2]
        self.originSmallMarker3D_Z = originSmallMarker3DDic[1][2]

        originBigMarker3D_points = np.array([
            originBigMarker3DDic[1], 
            originBigMarker3DDic[2], 
            originBigMarker3DDic[3], 
            originBigMarker3DDic[4], 
            originBigMarker3DDic[5]
        ])
        originSmallMarker3D_points = np.array([
            originSmallMarker3DDic[1],
            originSmallMarker3DDic[2],
            originSmallMarker3DDic[3],
            originSmallMarker3DDic[4],
            originSmallMarker3DDic[5]
        ])
        # -----------------------------------------------------------------
        # 3D空间中的世界坐标
        # 这是第一个视�?
        big_3DMarker1_source_points = np.array([
            self.bigMarker3DDic1[1], 
            self.bigMarker3DDic1[2], 
            self.bigMarker3DDic1[3], 
            self.bigMarker3DDic1[4], 
            self.bigMarker3DDic1[5]
        ])
        
        small_3DMarker1_source_points = np.array([
            self.smallMarker3DDic1[1], 
            self.smallMarker3DDic1[2], 
            self.smallMarker3DDic1[3], 
            self.smallMarker3DDic1[4], 
            self.smallMarker3DDic1[5]
        ])

        self.M3D2DRigidMatrixsBig1 = self.logic.getRigidMatrix(big_3DMarker1_source_points, originBigMarker3D_points)
        self.M2D3DRigidMatrixsBig1 = self.logic.getRigidMatrix(originBigMarker3D_points, big_3DMarker1_source_points)
        self.M3D2DRigidMatrixsSmall1 = self.logic.getRigidMatrix(small_3DMarker1_source_points, originSmallMarker3D_points)
        self.M2D3DRigidMatrixsSmall1 = self.logic.getRigidMatrix(originSmallMarker3D_points, small_3DMarker1_source_points)

        # 这是第二个视�?
        big_3DMarker2_source_points = np.array([
            self.bigMarker3DDic2[1], 
            self.bigMarker3DDic2[2], 
            self.bigMarker3DDic2[3], 
            self.bigMarker3DDic2[4], 
            self.bigMarker3DDic2[5]
        ])
        
        small_3DMarker2_source_points = np.array([
            self.smallMarker3DDic2[1], 
            self.smallMarker3DDic2[2], 
            self.smallMarker3DDic2[3], 
            self.smallMarker3DDic2[4], 
            self.smallMarker3DDic2[5]
        ])

        self.M3D2DRigidMatrixsBig2 = self.logic.getRigidMatrix(big_3DMarker2_source_points, originBigMarker3D_points)
        self.M2D3DRigidMatrixsBig2 = self.logic.getRigidMatrix(originBigMarker3D_points, big_3DMarker2_source_points)
        self.M3D2DRigidMatrixsSmall2 = self.logic.getRigidMatrix(small_3DMarker2_source_points, originSmallMarker3D_points)
        self.M2D3DRigidMatrixsSmall2 = self.logic.getRigidMatrix(originSmallMarker3D_points, small_3DMarker2_source_points)


        # 这是第三个视�?
        big_3DMarker3_source_points = np.array([
            self.bigMarker3DDic3[1], 
            self.bigMarker3DDic3[2], 
            self.bigMarker3DDic3[3], 
            self.bigMarker3DDic3[4], 
            self.bigMarker3DDic3[5]
        ])
        
        small_3DMarker3_source_points = np.array([
            self.smallMarker3DDic3[1], 
            self.smallMarker3DDic3[2], 
            self.smallMarker3DDic3[3], 
            self.smallMarker3DDic3[4], 
            self.smallMarker3DDic3[5]
        ])

        self.M3D2DRigidMatrixsBig3 = self.logic.getRigidMatrix(big_3DMarker3_source_points, originBigMarker3D_points)
        self.M2D3DRigidMatrixsBig3 = self.logic.getRigidMatrix(originBigMarker3D_points, big_3DMarker3_source_points)
        self.M3D2DRigidMatrixsSmall3 = self.logic.getRigidMatrix(small_3DMarker3_source_points, originSmallMarker3D_points)
        self.M2D3DRigidMatrixsSmall3 = self.logic.getRigidMatrix(originSmallMarker3D_points, small_3DMarker3_source_points)

        # *******************仿射变换（替代原透视变换�?******************
        # 使用出厂设置�?marker 坐标，全�?5 个点
        # 正交投影下平面→图像映射为仿射变换（6 DOF），
        # 使用 5 个点进行最小二乘拟合，�?4 点透视变换更鲁棒�?
        # -----------------------------------------------------------------
        originBigMarker3DDic = GenerateMarkers().bigMarker3DDic
        originSmallMarker3DDic = GenerateMarkers().smallMarker3DDic
        originBigMarker3D_4points = np.array([
            originBigMarker3DDic[1][0:2],
            originBigMarker3DDic[2][0:2], 
            originBigMarker3DDic[3][0:2], 
            originBigMarker3DDic[4][0:2], 
            originBigMarker3DDic[5][0:2]
        ], dtype=np.float32)
        originSmallMarker3D_4points = np.array([
            originSmallMarker3DDic[1][0:2],
            originSmallMarker3DDic[2][0:2],
            originSmallMarker3DDic[3][0:2],
            originSmallMarker3DDic[4][0:2],
            originSmallMarker3DDic[5][0:2]
        ], dtype=np.float32)

        # 2D 图像中的像素坐标
        # 这是第一个视�?
        big_2DMarker1_source_4points = np.array([
            get_key_by_value(self.bigMarkersSort1, 1)[0:2],
            get_key_by_value(self.bigMarkersSort1, 2)[0:2], 
            get_key_by_value(self.bigMarkersSort1, 3)[0:2], 
            get_key_by_value(self.bigMarkersSort1, 4)[0:2], 
            get_key_by_value(self.bigMarkersSort1, 5)[0:2], 
        ], dtype=np.float32)

        small_2DMarker1_source_4points = np.array([
            get_key_by_value(self.smallMarkersSort1, 1)[0:2],
            get_key_by_value(self.smallMarkersSort1, 2)[0:2], 
            get_key_by_value(self.smallMarkersSort1, 3)[0:2], 
            get_key_by_value(self.smallMarkersSort1, 4)[0:2], 
            get_key_by_value(self.smallMarkersSort1, 5)[0:2], 
        ], dtype=np.float32)

        # 这是第二个视�?
        big_2DMarker2_source_4points = np.array([
            get_key_by_value(self.bigMarkersSort2, 1)[0:2],
            get_key_by_value(self.bigMarkersSort2, 2)[0:2], 
            get_key_by_value(self.bigMarkersSort2, 3)[0:2], 
            get_key_by_value(self.bigMarkersSort2, 4)[0:2], 
            get_key_by_value(self.bigMarkersSort2, 5)[0:2], 
        ], dtype=np.float32)

        small_2DMarker2_source_4points = np.array([
            get_key_by_value(self.smallMarkersSort2, 1)[0:2],
            get_key_by_value(self.smallMarkersSort2, 2)[0:2], 
            get_key_by_value(self.smallMarkersSort2, 3)[0:2], 
            get_key_by_value(self.smallMarkersSort2, 4)[0:2], 
            get_key_by_value(self.smallMarkersSort2, 5)[0:2], 
        ], dtype=np.float32)

        # 这是第三个视�?
        big_2DMarker3_source_4points = np.array([
            get_key_by_value(self.bigMarkersSort3, 1)[0:2],
            get_key_by_value(self.bigMarkersSort3, 2)[0:2], 
            get_key_by_value(self.bigMarkersSort3, 3)[0:2], 
            get_key_by_value(self.bigMarkersSort3, 4)[0:2], 
            get_key_by_value(self.bigMarkersSort3, 5)[0:2], 
        ], dtype=np.float32)

        small_2DMarker3_source_4points = np.array([
            get_key_by_value(self.smallMarkersSort3, 1)[0:2],
            get_key_by_value(self.smallMarkersSort3, 2)[0:2], 
            get_key_by_value(self.smallMarkersSort3, 3)[0:2], 
            get_key_by_value(self.smallMarkersSort3, 4)[0:2], 
            get_key_by_value(self.smallMarkersSort3, 5)[0:2], 
        ], dtype=np.float32)

        projection_mode = self.projectionMode

        try:
            self.M3D2DPerspectiveMatrixsBig1 = self.logic.getPerspectiveTransform(
                originBigMarker3D_4points,
                big_2DMarker1_source_4points,
                projection_mode,
            )
            self.M2D3DPerspectiveMatrixsBig1 = self.logic.getPerspectiveTransform(
                big_2DMarker1_source_4points,
                originBigMarker3D_4points,
                projection_mode,
            )
            self.M3D2DPerspectiveMatrixsSmall1 = self.logic.getPerspectiveTransform(
                originSmallMarker3D_4points,
                small_2DMarker1_source_4points,
                projection_mode,
            )
            self.M2D3DPerspectiveMatrixsSmall1 = self.logic.getPerspectiveTransform(
                small_2DMarker1_source_4points,
                originSmallMarker3D_4points,
                projection_mode,
            )
            self.M3D2DPerspectiveMatrixsBig2 = self.logic.getPerspectiveTransform(
                originBigMarker3D_4points,
                big_2DMarker2_source_4points,
                projection_mode,
            )
            self.M2D3DPerspectiveMatrixsBig2 = self.logic.getPerspectiveTransform(
                big_2DMarker2_source_4points,
                originBigMarker3D_4points,
                projection_mode,
            )
            self.M3D2DPerspectiveMatrixsSmall2 = self.logic.getPerspectiveTransform(
                originSmallMarker3D_4points,
                small_2DMarker2_source_4points,
                projection_mode,
            )
            self.M2D3DPerspectiveMatrixsSmall2 = self.logic.getPerspectiveTransform(
                small_2DMarker2_source_4points,
                originSmallMarker3D_4points,
                projection_mode,
            )
            self.M3D2DPerspectiveMatrixsBig3 = self.logic.getPerspectiveTransform(
                originBigMarker3D_4points,
                big_2DMarker3_source_4points,
                projection_mode,
            )
            self.M2D3DPerspectiveMatrixsBig3 = self.logic.getPerspectiveTransform(
                big_2DMarker3_source_4points,
                originBigMarker3D_4points,
                projection_mode,
            )
            self.M3D2DPerspectiveMatrixsSmall3 = self.logic.getPerspectiveTransform(
                originSmallMarker3D_4points,
                small_2DMarker3_source_4points,
                projection_mode,
            )
            self.M2D3DPerspectiveMatrixsSmall3 = self.logic.getPerspectiveTransform(
                small_2DMarker3_source_4points,
                originSmallMarker3D_4points,
                projection_mode,
            )
        except Exception as e:
            self._error(f"Failed to compute projection matrices (mode: {projection_mode})", detailedText=str(e))
            return False

        if projection_mode == "perspective":
            if not self._compute_perspective_calibrations():
                return False
            self.orthographicViewCalibs = {}
        else:
            self.perspectiveViewCalibs = {}

        if projection_mode == "orthographic":
            if not self._compute_orthographic_calibrations():
                return False
        else:
            self.orthographicViewCalibs = {}

        return True


    def _apply_transform_to_markers(self, transform_name: str):
        """将 `_apply_transform_to_markers` 相关配置应用到当前场景。"""
        marker_model_node = self._getMarkersModelNode()
        if marker_model_node is None:
            self._error("markers model not found, please click showMarker first")
            return
        transform_node = slicer.mrmlScene.GetFirstNodeByName(transform_name)
        if transform_node is None:
            self._error(f"Transform node not found: {transform_name}")
            return
        marker_model_node.SetAndObserveTransformNodeID(transform_node.GetID())
        marker_model_node.SetDisplayVisibility(True)
        self._select_transform_in_transforms_module(transform_node)

    def _select_transform_in_transforms_module(self, transform_node):
        """在 Transforms 模块界面中选中指定变换节点，方便用户直接调整参数。"""
        try:
            module_widget = slicer.modules.transforms.widgetRepresentation()
            if module_widget is None:
                return
            selector = module_widget.findChild(slicer.qMRMLNodeComboBox, "TransformNodeSelector")
            if selector is None:
                return
            if hasattr(selector, "setCurrentNode"):
                selector.setCurrentNode(transform_node)
            elif hasattr(selector, "setCurrentNodeID"):
                selector.setCurrentNodeID(transform_node.GetID())
        except Exception:
            return

    def onMarkers1Button(self):
        """界面回调：执行 `onMarkers1Button` 对应的交互处理流程。"""
        self._apply_transform_to_markers("LinearTransform")

    def onMarkers2Button(self):
        """界面回调：执行 `onMarkers2Button` 对应的交互处理流程。"""
        self._apply_transform_to_markers("LinearTransform_1")

    def onMarkers3Button(self):
        """界面回调：执行 `onMarkers3Button` 对应的交互处理流程。"""
        self._apply_transform_to_markers("LinearTransform_2")

    def onOpenTransforms(self):
        """界面回调：执行 `onOpenTransforms` 对应的交互处理流程。"""
        self._open_transforms_module()

    def initLightVec(self):
        """初始化 Red/Green/Yellow 三视角的光线方向向量，为后续求交与重建提供几何基础。"""
        if self.projectionMode in ("perspective", "orthographic"):
            def get_ray(view_index: int, p2d: np.array):
                if self.projectionMode == "perspective":
                    return self._pixel_to_world_ray(view_index, p2d)
                return self._ortho_pixel_to_world_ray(view_index, p2d)

            markerNode1 = slicer.mrmlScene.GetFirstNodeByName("markers1")
            p1 = np.array(markerNode1.GetNthControlPointPosition(0)[0:2])
            o1, d1 = get_ray(1, p1)
            self.p3DBigRed = self.logic.rayPlaneIntersection(o1, d1, self.bigMarker3DDic1[1], self.bigMarker3DDic1[2], self.bigMarker3DDic1[3])
            self.p3DSmallRed = self.logic.rayPlaneIntersection(o1, d1, self.smallMarker3DDic1[1], self.smallMarker3DDic1[2], self.smallMarker3DDic1[3])
            if self.p3DBigRed is None or self.p3DSmallRed is None:
                self._error("Failed to intersect Red ray with marker planes in perspective mode")
                return
            self.red3DVec = np.array(self.p3DBigRed) - np.array(self.p3DSmallRed)

            markerNode2 = slicer.mrmlScene.GetFirstNodeByName("markers2")
            p2 = np.array(markerNode2.GetNthControlPointPosition(0)[0:2])
            o2, d2 = get_ray(2, p2)
            self.p3DBigGreen = self.logic.rayPlaneIntersection(o2, d2, self.bigMarker3DDic2[1], self.bigMarker3DDic2[2], self.bigMarker3DDic2[3])
            self.p3DSmallGreen = self.logic.rayPlaneIntersection(o2, d2, self.smallMarker3DDic2[1], self.smallMarker3DDic2[2], self.smallMarker3DDic2[3])
            if self.p3DBigGreen is None or self.p3DSmallGreen is None:
                self._error("Failed to intersect Green ray with marker planes in perspective mode")
                return
            self.green3DVec = np.array(self.p3DBigGreen) - np.array(self.p3DSmallGreen)

            markerNode3 = slicer.mrmlScene.GetFirstNodeByName("markers3")
            p3 = np.array(markerNode3.GetNthControlPointPosition(0)[0:2])
            o3, d3 = get_ray(3, p3)
            self.p3DBigYellow = self.logic.rayPlaneIntersection(o3, d3, self.bigMarker3DDic3[1], self.bigMarker3DDic3[2], self.bigMarker3DDic3[3])
            self.p3DSmallYellow = self.logic.rayPlaneIntersection(o3, d3, self.smallMarker3DDic3[1], self.smallMarker3DDic3[2], self.smallMarker3DDic3[3])
            if self.p3DBigYellow is None or self.p3DSmallYellow is None:
                self._error("Failed to intersect Yellow ray with marker planes in perspective mode")
                return
            self.yellow3DVec = np.array(self.p3DBigYellow) - np.array(self.p3DSmallYellow)
            return

        # 计算第一个视图的光线向量
        # 获取2D 平面上的一个点坐标
        markerNode1 = slicer.mrmlScene.GetFirstNodeByName("markers1")
        p1 = markerNode1.GetNthControlPointPosition(0)[0:2]
        p1 = np.array(p1)
        p1BigRed = self.logic.twoD2threeD(p1, self.M2D3DPerspectiveMatrixsBig1, self.M2D3DRigidMatrixsBig1, self.originBigMarker3D_Z)
        p1SmallRed = self.logic.twoD2threeD(p1, self.M2D3DPerspectiveMatrixsSmall1, self.M2D3DRigidMatrixsSmall1, self.originSmallMarker3D_Z)
        self.red3DVec = p1BigRed - p1SmallRed

        # 计算第二个视图的光线向量
        # 获取2D 平面上的一个点坐标
        markerNode2 = slicer.mrmlScene.GetFirstNodeByName("markers2")
        p2 = markerNode2.GetNthControlPointPosition(0)[0:2]
        p2 = np.array(p2)
        p2BigGreen = self.logic.twoD2threeD(p2, self.M2D3DPerspectiveMatrixsBig2, self.M2D3DRigidMatrixsBig2, self.originBigMarker3D_Z)
        p2SmallGreen = self.logic.twoD2threeD(p2, self.M2D3DPerspectiveMatrixsSmall2, self.M2D3DRigidMatrixsSmall2, self.originSmallMarker3D_Z)
        self.green3DVec = p2BigGreen - p2SmallGreen

        # 计算第三个视图的光线向量
        # 获取2D 平面上的一个点坐标
        markerNode3 = slicer.mrmlScene.GetFirstNodeByName("markers3")
        p3 = markerNode3.GetNthControlPointPosition(0)[0:2]
        p3 = np.array(p3)
        p3BigYellow = self.logic.twoD2threeD(p3, self.M2D3DPerspectiveMatrixsBig3, self.M2D3DRigidMatrixsBig3, self.originBigMarker3D_Z)
        p3SmallYellow = self.logic.twoD2threeD(p3, self.M2D3DPerspectiveMatrixsSmall3, self.M2D3DRigidMatrixsSmall3, self.originSmallMarker3D_Z)
        self.yellow3DVec = p3BigYellow - p3SmallYellow

    def onTwoD2ThreeDRed(self):
        """Timed wrapper for the Red-view 2D to 3D step."""
        return self._run_timed_step("red_push_ms", self._onTwoD2ThreeDRed_impl)

    def _onTwoD2ThreeDRed_impl(self):
        """处理 Red 视图 2D 点位：构造射线与 Green marker 平面求交，并在 Green 视图生成约束线。"""
        if not self._require_markers_sorted():
            return
        markupNode = self.ui.Red2DPSelector.currentNode()
        if not markupNode or markupNode.GetNumberOfControlPoints() < 1:
            self._error("Please add a 2D point in Red view first")
            return
        # �?Red 视图上的点只显示�?Red 视图
        displayNode = markupNode.GetDisplayNode()
        displayNode.SetVisibility(True)
        displayNode.SetViewNodeIDs(["vtkMRMLSliceNodeRed"])
        displayNode.SetVisibility3D(False)
        # displayNode.SetGlyphScale(0.6)
        displayNode.SetSelectedColor([0.941, 0.902, 0.549])

        clean_p2d = np.array(markupNode.GetNthControlPointPosition(0)[0:2], dtype=np.float64)
        try:
            run_state = self._prepare_controlled_perturbation_run_for_red(clean_p2d)
        except Exception as exc:
            self._reset_controlled_perturbation_run_state(clear_summary=False)
            self.controlledPerturbationLastAppliedSummary = "Run preparation failed"
            self._update_controlled_perturbation_status()
            self._error("Failed to prepare controlled perturbation run", detailedText=str(exc))
            return
        if isinstance(run_state, dict) and run_state.get("noise_type") == "target-only":
            p2D = np.array(run_state["red_point_slicer"], dtype=np.float64)
        else:
            p2D = clean_p2d
        if self.projectionMode in ("perspective", "orthographic"):
            if self.projectionMode == "perspective":
                ray_origin, ray_dir = self._pixel_to_world_ray(1, p2D)
            else:
                ray_origin, ray_dir = self._ortho_pixel_to_world_ray(1, p2D)
            self.p3DBigRed = self.logic.rayPlaneIntersection(
                ray_origin,
                ray_dir,
                self.bigMarker3DDic1[1],
                self.bigMarker3DDic1[2],
                self.bigMarker3DDic1[3],
            )
            self.p3DSmallRed = self.logic.rayPlaneIntersection(
                ray_origin,
                ray_dir,
                self.smallMarker3DDic1[1],
                self.smallMarker3DDic1[2],
                self.smallMarker3DDic1[3],
            )
        else:
            self.p3DBigRed = self.logic.twoD2threeD(p2D, self.M2D3DPerspectiveMatrixsBig1, self.M2D3DRigidMatrixsBig1, self.originBigMarker3D_Z)
            self.p3DSmallRed = self.logic.twoD2threeD(p2D, self.M2D3DPerspectiveMatrixsSmall1, self.M2D3DRigidMatrixsSmall1, self.originSmallMarker3D_Z)

        # 调试显示 #########################
        # lineNode = slicer.mrmlScene.GetFirstNodeByName("TMPRedLight3D")
        # if lineNode == None:
        #     lineNode = slicer.vtkMRMLMarkupsLineNode()
        #     lineNode.SetName("TMPRedLight3D")
        #     slicer.mrmlScene.AddNode(lineNode)
        #     lineNode.AddControlPoint(self.p3DBigRed)
        #     lineNode.AddControlPoint(self.p3DSmallRed)
        # else:
        #     lineNode.SetNthControlPointPosition(0, self.p3DBigRed)
        #     lineNode.SetNthControlPointPosition(1, self.p3DSmallRed)
        ###################################

        # 计算光线在Green 视图marker 平面�?big �?small 的交�?
        line_p1, line_p2 = np.array(self.p3DBigRed), np.array(self.p3DSmallRed)

        bigPlane_p1 = np.array(self.bigMarker3DDic2[1])
        bigPlane_p2 = np.array(self.bigMarker3DDic2[2])
        bigPlane_p3 = np.array(self.bigMarker3DDic2[3])

        smallPlane_p1 = np.array(self.smallMarker3DDic2[1])
        smallPlane_p2 = np.array(self.smallMarker3DDic2[2])
        smallPlane_p3 = np.array(self.smallMarker3DDic2[3])

        if self.debugVisualization:
            self._createOrUpdatePlaneModel(
                "vis_GreenBigPlane",
                bigPlane_p1, bigPlane_p2, bigPlane_p3,
                color=(0.0, 0.7, 0.7),
                opacity=0.2
            )
            self._createOrUpdatePlaneModel(
                "vis_GreenSmallPlane",
                smallPlane_p1, smallPlane_p2, smallPlane_p3,
                color=(0.0, 0.4, 0.4),
                opacity=0.2
            )

        bigIntersectionP3D = self.logic.line2plane_intersection(line_p1, line_p2, bigPlane_p1, bigPlane_p2, bigPlane_p3)
        smallIntersectionP3D = self.logic.line2plane_intersection(line_p1, line_p2, smallPlane_p1, smallPlane_p2, smallPlane_p3)
        if bigIntersectionP3D is None or smallIntersectionP3D is None:
            self._error("Failed to intersect ray with Green marker planes")
            return

        # Debug visualization: Red ray and intersections
        if self.debugVisualization:
            # Red ray line (extended)
            redRayPoints = self._getExtendedLinePoints(self.p3DBigRed, self.p3DSmallRed)
            self._createOrUpdateVisualizationNode(
                "vis_RedRay", 
                nodeType="MarkupsLine",
                color=(1, 0, 0),  # Red
                linePoints=redRayPoints
            )
            
            # Intersection points
            if bigIntersectionP3D is not None:
                self._createOrUpdateVisualizationNode(
                    "vis_RedBigIntersection",
                    position=bigIntersectionP3D,
                    color=(1, 0.5, 0.5),  # Light red
                    nodeType="MarkupsFiducial",
                    glyphScale=1.0
                )
            
            if smallIntersectionP3D is not None:
                self._createOrUpdateVisualizationNode(
                    "vis_RedSmallIntersection",
                    position=smallIntersectionP3D,
                    color=(1, 0.5, 0.5),  # Light red
                    nodeType="MarkupsFiducial",
                    glyphScale=1.0
                )

        # 调试显示  #########################
        # markupsNode1 = slicer.vtkMRMLMarkupsFiducialNode()
        # markupsNode1.SetName("TMPintersectionP")

        # slicer.mrmlScene.AddNode(markupsNode1)

        # n = markupsNode1.AddControlPoint(bigIntersectionP3D)
        # markupsNode1.SetNthControlPointLabel(n, "Big")
        # n = markupsNode1.AddControlPoint(smallIntersectionP3D)
        # markupsNode1.SetNthControlPointLabel(n, "Small")
        ####################################

        if self.projectionMode == "perspective":
            bigIntersectionP2DGreen = self._project_world_point_to_view(2, bigIntersectionP3D)
            smallIntersectionP2DGreen = self._project_world_point_to_view(2, smallIntersectionP3D)
        elif self.projectionMode == "orthographic":
            bigIntersectionP2DGreen = self._ortho_project_world_point_to_view(2, bigIntersectionP3D)
            smallIntersectionP2DGreen = self._ortho_project_world_point_to_view(2, smallIntersectionP3D)
        else:
            bigIntersectionP2DGreen = self.logic.threeD2twoD(bigIntersectionP3D, self.M3D2DRigidMatrixsBig2, self.M3D2DPerspectiveMatrixsBig2)
            smallIntersectionP2DGreen = self.logic.threeD2twoD(smallIntersectionP3D, self.M3D2DRigidMatrixsSmall2, self.M3D2DPerspectiveMatrixsSmall2)

        # 计算在绿色窗口中与边界的交点
        greenImageNode = slicer.mrmlScene.GetFirstNodeByName("shot2")
        bbox = [0,0,0,0,0,0]
        greenImageNode.GetBounds(bbox)
        xmin, xmax, ymin, ymax, zmin, zmax = bbox
        # print(bbox)
        line1 = (np.array([xmin, ymin]), np.array([xmin, ymax]))
        line2 = (np.array([xmin, ymin]), np.array([xmax, ymin]))
        line3 = (np.array([xmax, ymax]), np.array([xmin, ymax]))
        line4 = (np.array([xmax, ymax]), np.array([xmax, ymin]))
        p1 = self.logic.line2line_intersection(bigIntersectionP2DGreen, smallIntersectionP2DGreen, line1[0], line1[1])
        # print("------")
        # print(bigIntersectionP2DGreen)
        # print(smallIntersectionP2DGreen)
        # print(line1[0])
        # print(line1[1])
        # print("---------")
        p2 = self.logic.line2line_intersection(bigIntersectionP2DGreen, smallIntersectionP2DGreen, line2[0], line2[1])
        p3 = self.logic.line2line_intersection(bigIntersectionP2DGreen, smallIntersectionP2DGreen, line3[0], line3[1])
        p4 = self.logic.line2line_intersection(bigIntersectionP2DGreen, smallIntersectionP2DGreen, line4[0], line4[1])
        
        # print(p1)
        # print(p2)
        # print(p3)
        # print(p4)

        showPs = [] 
        if isinstance(p1, np.ndarray):
            if self.logic.isPinLine(p1, line1[0], line1[1]):
                showPs.append(p1)
        if isinstance(p2, np.ndarray):
            if self.logic.isPinLine(p2, line2[0], line2[1]):
                showPs.append(p2)
        if isinstance(p3, np.ndarray):
            if self.logic.isPinLine(p3, line3[0], line3[1]):
                showPs.append(p3)
        if isinstance(p4, np.ndarray):
            if self.logic.isPinLine(p4, line4[0], line4[1]):
                showPs.append(p4)

        # 只在Green 视图中显示线
        if len(showPs) < 2:
            self._error("计算得到的边界交点不足，无法绘制 GreenLine2D")
            return
        lineNodeGreen = slicer.mrmlScene.GetFirstNodeByName("GreenLine2D")
        if lineNodeGreen == None:
            lineNodeGreen = slicer.vtkMRMLMarkupsLineNode()
            lineNodeGreen.SetName("GreenLine2D")
            slicer.mrmlScene.AddNode(lineNodeGreen)
            lineNodeGreen.CreateDefaultDisplayNodes()
            displayNode = lineNodeGreen.GetDisplayNode()
            displayNode.SetVisibility(True)
            displayNode.SetViewNodeIDs(["vtkMRMLSliceNodeGreen"])
            displayNode.SetVisibility3D(False)
            displayNode.SetGlyphScale(0.3)
            displayNode.SetSelectedColor([0.862, 0.078, 0.235])

            lineNodeGreen.AddControlPoint(showPs[0][0], showPs[0][1], 0)
            lineNodeGreen.AddControlPoint(showPs[1][0], showPs[1][1], 0)
        else:
            lineNodeGreen.SetNthControlPointPosition(0, showPs[0][0], showPs[0][1], 0)
            lineNodeGreen.SetNthControlPointPosition(1, showPs[1][0], showPs[1][1], 0)


    def onTwoD2ThreeDGreen(self):
        """Timed wrapper for the Green-view 2D to 3D step."""
        return self._run_timed_step("green_push_ms", self._onTwoD2ThreeDGreen_impl)

    def _onTwoD2ThreeDGreen_impl(self):
        """处理 Green 视图 2D 点位：合并约束线与平面求交，重建目标 3D 点并投影到 Yellow 视图。"""
        self.lastRayGapMmRaw = None
        markupNode = self.ui.Green2DPSelector.currentNode()
        if not markupNode or markupNode.GetNumberOfControlPoints() < 1:
            self._error("Please add a 2D point in Green view first")
            return
        # �?Green 视图上的点只显示 Green 视图
        displayNode = markupNode.GetDisplayNode()
        displayNode.SetVisibility(True)
        displayNode.SetViewNodeIDs(["vtkMRMLSliceNodeGreen"])
        displayNode.SetVisibility3D(False)
        # displayNode.SetGlyphScale(0.6)
        displayNode.SetSelectedColor([0.392, 0.584, 0.929])

        run_state = self._require_controlled_perturbation_run_for_green()
        if self.controlledPerturbationEnabled and run_state is None:
            return
        clean_green_point = np.array(markupNode.GetNthControlPointPosition(0), dtype=np.float64)
        p3D = clean_green_point.copy()
        using_target_noise = bool(
            isinstance(run_state, dict) and run_state.get("noise_type") == "target-only"
        )
        if using_target_noise:
            perturbed_green_xy, green_delta = self._perturb_slicer_2d_point(
                clean_green_point[0:2],
                run_state.get("sigma_px", 0.0),
            )
            p3D[0] = float(perturbed_green_xy[0])
            p3D[1] = float(perturbed_green_xy[1])
            run_state["green_delta_slicer"] = green_delta
        lineNodeGreen = slicer.mrmlScene.GetFirstNodeByName("GreenLine2D")
        lineP1 = np.array([0.0, 0.0, 0.0])
        lineP2 = np.array([0.0, 0.0, 0.0])
        if lineNodeGreen == None:
            self._error("Please click redPush in Red view first to create GreenLine2D")
            return
        else:
            lineStartP = lineNodeGreen.GetLineStartPositionWorld()
            lineEndP = lineNodeGreen.GetLineEndPositionWorld()
            lineP1[0], lineP1[1], lineP1[2] = lineStartP.GetX(), lineStartP.GetY(), lineStartP.GetZ()
            lineP2[0], lineP2[1], lineP2[2] = lineEndP.GetX(), lineEndP.GetY(), lineEndP.GetZ()

        if self.debugVisualization:
            redBigPlane_p1 = np.array(self.bigMarker3DDic1[1])
            redBigPlane_p2 = np.array(self.bigMarker3DDic1[2])
            redBigPlane_p3 = np.array(self.bigMarker3DDic1[3])

            redSmallPlane_p1 = np.array(self.smallMarker3DDic1[1])
            redSmallPlane_p2 = np.array(self.smallMarker3DDic1[2])
            redSmallPlane_p3 = np.array(self.smallMarker3DDic1[3])

            self._createOrUpdatePlaneModel(
                "vis_RedBigPlane",
                redBigPlane_p1, redBigPlane_p2, redBigPlane_p3,
                color=(0.8, 0.2, 0.2),
                opacity=0.2,
                scale=self.debugPlaneScale
            )
            self._createOrUpdatePlaneModel(
                "vis_RedSmallPlane",
                redSmallPlane_p1, redSmallPlane_p2, redSmallPlane_p3,
                color=(0.5, 0.1, 0.1),
                opacity=0.2,
                scale=self.debugPlaneScale
            )
            
        # 计算手动添加的点到直线的最近点，将点自动移动到直线�?
        p3DNearest2Line = self.logic.pointNearest2Line(p3D, lineP1, lineP2)
        # 替换手动添加�?markupNode
        if self.controlledPerturbationEnabled and using_target_noise:
            run_state["green_point_slicer"] = np.array(p3D[0:2], dtype=np.float64)
            self._set_controlled_perturbation_preview_point(
                "ControlledPerturbationGreen2D",
                "vtkMRMLSliceNodeGreen",
                p3D[0:2],
                (0.1, 0.55, 0.85),
            )
        elif self.controlledPerturbationEnabled:
            self._set_controlled_perturbation_preview_point(
                "ControlledPerturbationGreen2D",
                "vtkMRMLSliceNodeGreen",
                None,
                (0.1, 0.55, 0.85),
            )
        else:
            markupNode.SetNthControlPointPosition(0, p3DNearest2Line)
            self._set_controlled_perturbation_preview_point(
                "ControlledPerturbationGreen2D",
                "vtkMRMLSliceNodeGreen",
                None,
                (0.1, 0.55, 0.85),
            )
        p2DNearest2Line = np.array(p3D[0:2], dtype=np.float64) if self.controlledPerturbationEnabled else p3DNearest2Line[0:2]
        if self.projectionMode in ("perspective", "orthographic"):
            if self.projectionMode == "perspective":
                ray_origin, ray_dir = self._pixel_to_world_ray(2, p2DNearest2Line)
            else:
                ray_origin, ray_dir = self._ortho_pixel_to_world_ray(2, p2DNearest2Line)
            self.p3DBigGreen = self.logic.rayPlaneIntersection(
                ray_origin,
                ray_dir,
                self.bigMarker3DDic2[1],
                self.bigMarker3DDic2[2],
                self.bigMarker3DDic2[3],
            )
            self.p3DSmallGreen = self.logic.rayPlaneIntersection(
                ray_origin,
                ray_dir,
                self.smallMarker3DDic2[1],
                self.smallMarker3DDic2[2],
                self.smallMarker3DDic2[3],
            )
            if self.p3DBigGreen is None or self.p3DSmallGreen is None:
                self._error("Failed to intersect Green perspective ray with marker planes")
                return
        else:
            self.p3DBigGreen = self.logic.twoD2threeD(p2DNearest2Line, self.M2D3DPerspectiveMatrixsBig2, self.M2D3DRigidMatrixsBig2, self.originBigMarker3D_Z)
            self.p3DSmallGreen = self.logic.twoD2threeD(p2DNearest2Line, self.M2D3DPerspectiveMatrixsSmall2, self.M2D3DRigidMatrixsSmall2, self.originSmallMarker3D_Z)

        # Debug visualization: Green ray and intersections with Red planes
        if self.debugVisualization:
            # Green ray line (extended)
            greenRayPoints = self._getExtendedLinePoints(self.p3DBigGreen, self.p3DSmallGreen, scale=self.debugRayScale)
            self._createOrUpdateVisualizationNode(
                "vis_GreenRay",
                nodeType="MarkupsLine",
                color=(0, 1, 0),  # Green
                linePoints=greenRayPoints
            )

            redBigPlane_p1 = np.array(self.bigMarker3DDic1[1])
            redBigPlane_p2 = np.array(self.bigMarker3DDic1[2])
            redBigPlane_p3 = np.array(self.bigMarker3DDic1[3])

            redSmallPlane_p1 = np.array(self.smallMarker3DDic1[1])
            redSmallPlane_p2 = np.array(self.smallMarker3DDic1[2])
            redSmallPlane_p3 = np.array(self.smallMarker3DDic1[3])

            greenBigIntersectionP3D = self.logic.line2plane_intersection(
                np.array(self.p3DBigGreen), np.array(self.p3DSmallGreen),
                redBigPlane_p1, redBigPlane_p2, redBigPlane_p3
            )
            greenSmallIntersectionP3D = self.logic.line2plane_intersection(
                np.array(self.p3DBigGreen), np.array(self.p3DSmallGreen),
                redSmallPlane_p1, redSmallPlane_p2, redSmallPlane_p3
            )

            if greenBigIntersectionP3D is not None:
                self._createOrUpdateVisualizationNode(
                    "vis_GreenBigIntersection",
                    position=greenBigIntersectionP3D,
                    color=(0.5, 1, 0.5),  # Light green
                    nodeType="MarkupsFiducial",
                    glyphScale=1.0
                )

            if greenSmallIntersectionP3D is not None:
                self._createOrUpdateVisualizationNode(
                    "vis_GreenSmallIntersection",
                    position=greenSmallIntersectionP3D,
                    color=(0.5, 1, 0.5),  # Light green
                    nodeType="MarkupsFiducial",
                    glyphScale=1.0
                )

        # 调试显示  ############################
        # lineNode = slicer.mrmlScene.GetFirstNodeByName("TMPGreenLight3D")
        # if lineNode == None:
        #     lineNode = slicer.vtkMRMLMarkupsLineNode()
        #     lineNode.SetName("TMPGreenLight3D")
        #     slicer.mrmlScene.AddNode(lineNode)
        #     lineNode.AddControlPoint(self.p3DBigGreen)
        #     lineNode.AddControlPoint(self.p3DSmallGreen)
        # else:
        #     lineNode.SetNthControlPointPosition(0, self.p3DBigGreen)
        #     lineNode.SetNthControlPointPosition(1, self.p3DSmallGreen)
        #######################################
        
        # 计算两条3D光线的空间交�?
        line1_p1, line1_p2 = np.array(self.p3DBigRed), np.array(self.p3DSmallRed)
        line2_p1, line2_p2 = np.array(self.p3DBigGreen), np.array(self.p3DSmallGreen)

        p3D, line_gap = self.logic.line2line_closest_midpoint3D(line1_p1, line1_p2, line2_p1, line2_p2)
        if p3D is None:
            self._error("Two 3D rays are nearly parallel; cannot stably compute TargetP3D")
            return
        self.p3DTarget = p3D
        self.lastRayGapMmRaw = float(line_gap) if line_gap is not None else None
        if line_gap is not None:
            line_gap_text = f"{line_gap:.4f} mm" if self.controlledPerturbationEnabled else f"{line_gap:.2f} mm"
            self.ui.lineGapDisplay.setText(line_gap_text)
            logging.info(f"Ray gap (closest distance): {line_gap:.4f} mm")
        
        # Debug visualization: TargetP3D calculation steps
        if self.debugVisualization:
            # Ray lines if not already shown
            if not slicer.mrmlScene.GetFirstNodeByName("vis_RedRay"):
                redRayPoints = self._getExtendedLinePoints(line1_p1, line1_p2, scale=self.debugRayScale)
                self._createOrUpdateVisualizationNode(
                    "vis_RedRay",
                    nodeType="MarkupsLine",
                    color=(1, 0, 0),  # Red
                    linePoints=redRayPoints
                )
            if not slicer.mrmlScene.GetFirstNodeByName("vis_GreenRay"):
                greenRayPoints = self._getExtendedLinePoints(line2_p1, line2_p2, scale=self.debugRayScale)
                self._createOrUpdateVisualizationNode(
                    "vis_GreenRay",
                    nodeType="MarkupsLine",
                    color=(0, 1, 0),  # Green
                    linePoints=greenRayPoints
                )
            
            # TargetP3D midpoint
            self._createOrUpdateVisualizationNode(
                "vis_TargetP3DMidpoint",
                position=p3D,
                color=(1, 1, 0),  # Yellow
                nodeType="MarkupsFiducial"
            )

            if hasattr(self, "yellow3DVec"):
                yellowRayPoints = self._getExtendedLinePoints(
                    p3D - self.yellow3DVec,
                    p3D + self.yellow3DVec,
                    scale=self.debugRayScale
                )
                self._createOrUpdateVisualizationNode(
                    "vis_YellowRay",
                    nodeType="MarkupsLine",
                    color=(1, 1, 0),
                    linePoints=yellowRayPoints
                )

                if hasattr(self, "bigMarker3DDic3") and hasattr(self, "smallMarker3DDic3"):
                    yellowBig_p1 = np.array(self.bigMarker3DDic3[1])
                    yellowBig_p2 = np.array(self.bigMarker3DDic3[2])
                    yellowBig_p3 = np.array(self.bigMarker3DDic3[3])
                    yellowSmall_p1 = np.array(self.smallMarker3DDic3[1])
                    yellowSmall_p2 = np.array(self.smallMarker3DDic3[2])
                    yellowSmall_p3 = np.array(self.smallMarker3DDic3[3])

                    yellowBigIntersectionP3D = self.logic.line2plane_intersection(
                        p3D - self.yellow3DVec,
                        p3D + self.yellow3DVec,
                        yellowBig_p1, yellowBig_p2, yellowBig_p3
                    )
                    yellowSmallIntersectionP3D = self.logic.line2plane_intersection(
                        p3D - self.yellow3DVec,
                        p3D + self.yellow3DVec,
                        yellowSmall_p1, yellowSmall_p2, yellowSmall_p3
                    )

                    if yellowBigIntersectionP3D is not None:
                        self._createOrUpdateVisualizationNode(
                            "vis_YellowBigIntersection",
                            position=yellowBigIntersectionP3D,
                            color=(1, 1, 0),
                            nodeType="MarkupsFiducial",
                            glyphScale=1.0
                        )
                    if yellowSmallIntersectionP3D is not None:
                        self._createOrUpdateVisualizationNode(
                            "vis_YellowSmallIntersection",
                            position=yellowSmallIntersectionP3D,
                            color=(1, 1, 0),
                            nodeType="MarkupsFiducial",
                            glyphScale=1.0
                        )

        if self.projectionMode == "perspective":
            p2DGreen_check = self._project_world_point_to_view(2, p3D)
        elif self.projectionMode == "orthographic":
            p2DGreen_check = self._ortho_project_world_point_to_view(2, p3D)
        else:
            p2DGreen_check = self.logic.threeD2twoDFor3DSpace(
                p3D,
                self.green3DVec,
                np.array(self.bigMarker3DDic2[1]),
                np.array(self.bigMarker3DDic2[2]),
                self.bigMarker3DDic2[3],
                self.M3D2DRigidMatrixsBig2,
                self.M3D2DPerspectiveMatrixsBig2,
            )
        reproj_err = np.linalg.norm(p2DGreen_check - p2DNearest2Line)
        logging.info(f"Green reprojection residual: {reproj_err:.4f} px")

        # 显示�?3D 空间的实际位置的点，该点是最终结果点
        markupNode3D = slicer.mrmlScene.GetFirstNodeByName("TargetP3D")
        if markupNode3D == None:
            markupNode3D = slicer.vtkMRMLMarkupsFiducialNode()
            markupNode3D.SetName("TargetP3D")
            slicer.mrmlScene.AddNode(markupNode3D)
            
            n = markupNode3D.AddControlPoint(p3D)
            markupNode3D.SetNthControlPointLabel(n, "TargetP")

            markupNode3D.CreateDefaultDisplayNodes()
            displayNode = markupNode3D.GetDisplayNode()
            displayNode.SetVisibility(True)
            displayNode.SetViewNodeIDs(["vtkMRMLViewNode1"])
            displayNode.SetVisibility3D(True)
            # displayNode.SetGlyphScale(0.9)
            displayNode.SetSelectedColor([0.502, 0, 0.502])
        else:
            markupNode3D.SetNthControlPointPosition(0, p3D)

        # 计算同时显示第三个视图上�?D�?
        if self.projectionMode == "perspective":
            intersectionP3DYellow = self._project_world_point_to_view(3, p3D)
        elif self.projectionMode == "orthographic":
            intersectionP3DYellow = self._ortho_project_world_point_to_view(3, p3D)
        else:
            intersectionP3DYellow = self.logic.threeD2twoDFor3DSpace(p3D, self.yellow3DVec, 
                                             np.array(self.bigMarker3DDic3[1]), np.array(self.bigMarker3DDic3[2]), np.array(self.bigMarker3DDic3[3]),
                                             self.M3D2DRigidMatrixsBig3, self.M3D2DPerspectiveMatrixsBig3)
        
        markupNodeYellow = slicer.mrmlScene.GetFirstNodeByName("TargetP2DYellow")
        if markupNodeYellow == None:
            markupNodeYellow = slicer.vtkMRMLMarkupsFiducialNode()
            markupNodeYellow.SetName("TargetP2DYellow")
            slicer.mrmlScene.AddNode(markupNodeYellow)

            n = markupNodeYellow.AddControlPoint(intersectionP3DYellow[0], intersectionP3DYellow[1], 0)
            markupNodeYellow.SetNthControlPointLabel(n, "TargetP")

            markupNodeYellow.CreateDefaultDisplayNodes()
            displayNode = markupNodeYellow.GetDisplayNode()
            displayNode.SetVisibility(True)
            displayNode.SetVisibility3D(False)
            displayNode.SetViewNodeIDs(["vtkMRMLSliceNodeYellow"])
            # displayNode.SetGlyphScale(0.3)
            displayNode.SetSelectedColor([0.18, 0.545, 0.341])
        else:
            markupNodeYellow.SetNthControlPointPosition(0, [intersectionP3DYellow[0], intersectionP3DYellow[1], 0])

        tre_point1 = slicer.mrmlScene.GetFirstNodeByName("testPoint")
        tre_point2 = slicer.mrmlScene.GetFirstNodeByName("TargetP3D")
        self._set_selector_current_node(self.ui.point1Selector, tre_point1)
        self._set_selector_current_node(self.ui.point2Selector, tre_point2)

        re_point1 = slicer.mrmlScene.GetFirstNodeByName("blackCenter3")
        re_point2 = slicer.mrmlScene.GetFirstNodeByName("TargetP2DYellow")
        self._set_selector_current_node(self.ui.reprojectionPoint1Selector, re_point1)
        self._set_selector_current_node(self.ui.reprojectionPoint2Selector, re_point2)

        if isinstance(run_state, dict):
            run_state["stage"] = "completed"
            self.controlledPerturbationLastAppliedSummary = f"Run #{run_state['run_id']} completed"
            self._update_controlled_perturbation_status()
            self.onCalculateTRE()
            self.onCalculateReprojectionError()


    def onTracing(self):
        """启动刀尖跟踪：注册控制点修改事件，让 3D 点在三个 2D 视图同步刷新。"""
        knifeNode = self.ui.knifeSelector.currentNode()
        if not knifeNode or knifeNode.GetNumberOfControlPoints() < 1:
            self._error("请先选择 knife 点并至少添加一个控制点")
            return
        self.tracingP3D(0, 0)      
        if self._knifeObserverTag is not None:
            try:
                knifeNode.RemoveObserver(self._knifeObserverTag)
            except Exception:
                pass
        self._knifeObserverTag = knifeNode.AddObserver(slicer.vtkMRMLMarkupsNode.PointModifiedEvent, self.tracingP3D)
        

    def tracingP3D(self, caller, event):
        """跟踪回调：将当前 knife 3D 点实时投影到 Red/Green/Yellow 视图。"""
        knifeNode = self.ui.knifeSelector.currentNode()    
        p3D = np.array(knifeNode.GetNthControlPointPosition(0))
        if self.projectionMode == "perspective":
            p2DRed = self._project_world_point_to_view(1, p3D)
            p2DGreen = self._project_world_point_to_view(2, p3D)
            p2DYellow = self._project_world_point_to_view(3, p3D)
        elif self.projectionMode == "orthographic":
            p2DRed = self._ortho_project_world_point_to_view(1, p3D)
            p2DGreen = self._ortho_project_world_point_to_view(2, p3D)
            p2DYellow = self._ortho_project_world_point_to_view(3, p3D)
        else:
            p2DRed = self.logic.threeD2twoDFor3DSpace(
                p3D,
                self.red3DVec,
                np.array(self.bigMarker3DDic1[1]),
                np.array(self.bigMarker3DDic1[2]),
                self.bigMarker3DDic1[3],
                self.M3D2DRigidMatrixsBig1,
                self.M3D2DPerspectiveMatrixsBig1,
            )
            p2DGreen = self.logic.threeD2twoDFor3DSpace(
                p3D,
                self.green3DVec,
                np.array(self.bigMarker3DDic2[1]),
                np.array(self.bigMarker3DDic2[2]),
                self.bigMarker3DDic2[3],
                self.M3D2DRigidMatrixsBig2,
                self.M3D2DPerspectiveMatrixsBig2,
            )
            p2DYellow = self.logic.threeD2twoDFor3DSpace(
                p3D,
                self.yellow3DVec,
                np.array(self.bigMarker3DDic3[1]),
                np.array(self.bigMarker3DDic3[2]),
                self.bigMarker3DDic3[3],
                self.M3D2DRigidMatrixsBig3,
                self.M3D2DPerspectiveMatrixsBig3,
            )
        
        # 显示追踪�?
        markupsNode1 = slicer.mrmlScene.GetFirstNodeByName("tracingRed2D")
        if markupsNode1 == None:
            markupsNode1 = slicer.vtkMRMLMarkupsFiducialNode()
            markupsNode1.SetName("tracingRed2D")
            slicer.mrmlScene.AddNode(markupsNode1)
            markupsNode1.CreateDefaultDisplayNodes()
            displayNode1 = markupsNode1.GetDisplayNode()
            displayNode1.SetVisibility(True)
            displayNode1.SetVisibility3D(False)
            # displayNode1.SetGlyphScale(0.3)
            displayNode1.SetViewNodeIDs(["vtkMRMLSliceNodeRed"])
            displayNode1.SetSelectedColor([0.294, 0, 0.509])
            n = markupsNode1.AddControlPoint(p2DRed[0], p2DRed[1], 0)
            markupsNode1.SetNthControlPointLabel(n, "tracingRed")
        else:
            markupsNode1.SetNthControlPointPosition(0, p2DRed[0], p2DRed[1], 0)
        
        markupsNode2 = slicer.mrmlScene.GetFirstNodeByName("tracingGreen2D")
        if markupsNode2 == None:
            markupsNode2 = slicer.vtkMRMLMarkupsFiducialNode()
            markupsNode2.SetName("tracingGreen2D")
            slicer.mrmlScene.AddNode(markupsNode2)
            markupsNode2.CreateDefaultDisplayNodes()
            displayNode2 = markupsNode2.GetDisplayNode()
            displayNode2.SetVisibility(True)
            displayNode2.SetVisibility3D(False)
            # displayNode2.SetGlyphScale(0.3)
            displayNode2.SetSelectedColor([0, 0, 0.549])
            displayNode2.SetViewNodeIDs(["vtkMRMLSliceNodeGreen"])
            n = markupsNode2.AddControlPoint(p2DGreen[0], p2DGreen[1], 0)
            markupsNode2.SetNthControlPointLabel(n, "tracingGreen")
        else:
            markupsNode2.SetNthControlPointPosition(0, p2DGreen[0], p2DGreen[1], 0)

        markupsNode3 = slicer.mrmlScene.GetFirstNodeByName("tracingYellow2D")
        if markupsNode3 == None:
            markupsNode3 = slicer.vtkMRMLMarkupsFiducialNode()
            markupsNode3.SetName("tracingYellow2D")
            slicer.mrmlScene.AddNode(markupsNode3) 
            markupsNode3.CreateDefaultDisplayNodes()   
            displayNode3 = markupsNode3.GetDisplayNode()    
            displayNode3.SetVisibility(True) 
            displayNode3.SetVisibility3D(False)
            # displayNode3.SetGlyphScale(0.3)
            displayNode3.SetSelectedColor([0.392, 0.584, 0.929])
            displayNode3.SetViewNodeIDs(["vtkMRMLSliceNodeYellow"])
            n = markupsNode3.AddControlPoint(p2DYellow[0], p2DYellow[1], 0)
            markupsNode3.SetNthControlPointLabel(n, "tracingYellow")
        else:
            markupsNode3.SetNthControlPointPosition(0, p2DYellow[0], p2DYellow[1], 0)


    def onCalculateTRE(self):
        """Timed wrapper for TRE computation."""
        return self._run_timed_step("tre_calc_ms", self._onCalculateTRE_impl)

    def _onCalculateTRE_impl(self):
        """计算两个选中点之间的 TRE（三维欧式距离），并写入 UI 与日志。"""
        self.lastTreMmRaw = None
        try:
            # Get the selected nodes
            point1Node = self.ui.point1Selector.currentNode()
            point2Node = self.ui.point2Selector.currentNode()
            
            # Validate that both nodes are selected
            if not point1Node:
                self._error("Please select Point 1")
                return
            if not point2Node:
                self._error("Please select Point 2")
                return
            
            # Get the number of control points
            if point1Node.GetNumberOfControlPoints() < 1:
                self._error("Point 1 has no fiducial points")
                return
            if point2Node.GetNumberOfControlPoints() < 1:
                self._error("Point 2 has no fiducial points")
                return
            
            # Get the coordinates of the first control point from each node
            pos1 = np.array(point1Node.GetNthControlPointPositionWorld(0))
            pos2 = np.array(point2Node.GetNthControlPointPositionWorld(0))
            
            # Calculate the Euclidean distance (TRE)
            tre = np.linalg.norm(pos2 - pos1)
            self.lastTreMmRaw = float(tre)
            
            # Display the result in the UI (formatted to 2 decimal places)
            self.ui.treValueDisplay.setText(f"{tre:.2f} mm")
            
            # Also log the result
            logging.info(f"TRE calculated: {tre:.4f} mm")
            logging.info(f"  Point 1: ({pos1[0]:.2f}, {pos1[1]:.2f}, {pos1[2]:.2f})")
            logging.info(f"  Point 2: ({pos2[0]:.2f}, {pos2[1]:.2f}, {pos2[2]:.2f})")
            
        except Exception as e:
            self._error(f"Error calculating TRE: {str(e)}", detailedText=f"{e}")

    def onCalculateReprojectionError(self):
        """Timed wrapper for reprojection-error computation."""
        return self._run_timed_step("reprojection_calc_ms", self._onCalculateReprojectionError_impl)

    def _onCalculateReprojectionError_impl(self):
        """计算两个选中 2D 点的重投影误差（像素距离），并写入 UI 与日志。"""
        self.lastReprojectionErrorPxRaw = None
        try:
            point1Node = self.ui.reprojectionPoint1Selector.currentNode()
            point2Node = self.ui.reprojectionPoint2Selector.currentNode()

            if not point1Node:
                self._error("Please select Point 1")
                return
            if not point2Node:
                self._error("Please select Point 2")
                return

            if point1Node.GetNumberOfControlPoints() < 1:
                self._error("Point 1 has no fiducial points")
                return
            if point2Node.GetNumberOfControlPoints() < 1:
                self._error("Point 2 has no fiducial points")
                return

            pos1 = np.array(point1Node.GetNthControlPointPosition(0))
            pos2 = np.array(point2Node.GetNthControlPointPosition(0))

            reproj_err = np.linalg.norm(pos2[:2] - pos1[:2])
            self.lastReprojectionErrorPxRaw = float(reproj_err)
            self.ui.reprojectionValueDisplay.setText(f"{reproj_err:.2f} px")

            logging.info(f"Reprojection error calculated: {reproj_err:.4f} px")
            logging.info(f"  Point 1: ({pos1[0]:.2f}, {pos1[1]:.2f}, {pos1[2]:.2f})")
            logging.info(f"  Point 2: ({pos2[0]:.2f}, {pos2[1]:.2f}, {pos2[2]:.2f})")

        except Exception as e:
            self._error(f"Error calculating reprojection error: {str(e)}", detailedText=f"{e}")

    def _widget_text(self, widget_name: str) -> str:
        """Read text-like content from a UI widget safely."""
        widget = getattr(self.ui, widget_name, None)
        if widget is None:
            return ""
        value = ""
        if hasattr(widget, "text"):
            value = widget.text
            if callable(value):
                value = value()
        elif hasattr(widget, "currentText"):
            value = widget.currentText
            if callable(value):
                value = value()
        if value is None:
            return ""
        return str(value).strip()

    def _initialize_controlled_perturbation_ui(self) -> None:
        """Apply default controlled perturbation UI values and refresh summary text."""
        if hasattr(self.ui, "controlledPerturbationNoiseTypeComboBox") and self.ui.controlledPerturbationNoiseTypeComboBox is not None:
            self.ui.controlledPerturbationNoiseTypeComboBox.setCurrentIndex(0)
        if hasattr(self.ui, "controlledPerturbationNoiseSigmaComboBox") and self.ui.controlledPerturbationNoiseSigmaComboBox is not None:
            self.ui.controlledPerturbationNoiseSigmaComboBox.setCurrentIndex(0)
        if hasattr(self.ui, "runControlledPerturbationButton") and self.ui.runControlledPerturbationButton is not None:
            self.ui.runControlledPerturbationButton.setChecked(False)
        self._update_controlled_perturbation_status()

    def _set_controlled_perturbation_preview_point(self, node_name: str, view_node_id: str, point2d, color) -> None:
        """Create/update a 2D preview point showing the perturbed target input used by the experiment."""
        node = slicer.mrmlScene.GetFirstNodeByName(node_name)
        if point2d is None:
            if node is not None and node.GetDisplayNode() is not None:
                node.GetDisplayNode().SetVisibility(False)
            return

        if node is None:
            node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", node_name)
            node.CreateDefaultDisplayNodes()

        display_node = node.GetDisplayNode()
        if display_node is not None:
            display_node.SetVisibility(True)
            display_node.SetVisibility3D(False)
            display_node.SetViewNodeIDs([view_node_id])
            display_node.SetPointLabelsVisibility(False)
            display_node.SetGlyphScale(0.8)
            display_node.SetSelectedColor(color)
            display_node.SetColor(color)

        point = np.array(point2d, dtype=float).flatten()
        point3d = [float(point[0]), float(point[1]), 0.0]
        if node.GetNumberOfControlPoints() < 1:
            node.AddControlPoint(point3d)
        else:
            node.SetNthControlPointPosition(0, point3d)

    def _reset_controlled_perturbation_run_state(self, clear_summary: bool = True) -> None:
        """Drop any prepared perturbation run context and hide preview points."""
        self.controlledPerturbationRunState = None
        if clear_summary:
            self.controlledPerturbationLastAppliedSummary = ""
        self._set_controlled_perturbation_preview_point(
            "ControlledPerturbationRed2D",
            "vtkMRMLSliceNodeRed",
            None,
            (0.85, 0.35, 0.1),
        )
        self._set_controlled_perturbation_preview_point(
            "ControlledPerturbationGreen2D",
            "vtkMRMLSliceNodeGreen",
            None,
            (0.1, 0.55, 0.85),
        )

    def _get_controlled_perturbation_noise_type(self) -> str:
        """Return the currently selected controlled perturbation noise type."""
        return self._widget_text("controlledPerturbationNoiseTypeComboBox") or "marker-only"

    def _get_controlled_perturbation_noise_sigma_px(self) -> float:
        """Return the currently selected controlled perturbation sigma in pixels."""
        sigma_text = self._widget_text("controlledPerturbationNoiseSigmaComboBox")
        sigma_value = self._extract_first_float(sigma_text)
        try:
            return float(sigma_value) if sigma_value != "" else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _update_controlled_perturbation_status(self) -> None:
        """Refresh cached perturbation option values and their UI summary."""
        self.controlledPerturbationNoiseType = self._get_controlled_perturbation_noise_type()
        self.controlledPerturbationNoiseSigmaPx = self._get_controlled_perturbation_noise_sigma_px()
        if hasattr(self.ui, "runControlledPerturbationButton") and self.ui.runControlledPerturbationButton is not None:
            self.controlledPerturbationEnabled = bool(self.ui.runControlledPerturbationButton.isChecked())
            button_text = (
                "Disable Perturbation"
                if self.controlledPerturbationEnabled
                else "Enable Perturbation"
            )
            self.ui.runControlledPerturbationButton.setText(button_text)
        sigma_label = self._widget_text("controlledPerturbationNoiseSigmaComboBox") or "0 px"
        state_text = "Enabled" if self.controlledPerturbationEnabled else "Disabled"
        status_text = (
            f"Status: {state_text} | "
            f"Type: {self.controlledPerturbationNoiseType} | "
            f"Sigma: {sigma_label}"
        )
        if self.controlledPerturbationLastAppliedSummary:
            status_text = f"{status_text}\n{self.controlledPerturbationLastAppliedSummary}"
        if hasattr(self.ui, "controlledPerturbationStatusLabel") and self.ui.controlledPerturbationStatusLabel is not None:
            self.ui.controlledPerturbationStatusLabel.setText(status_text)
            self.ui.controlledPerturbationStatusLabel.setStyleSheet(
                "color: #2b8a3e; font-weight: bold;" if self.controlledPerturbationEnabled else "color: #c92a2a;"
            )

    def _perturb_slicer_2d_point(self, point2d, sigma_px: float):
        """Apply isotropic Gaussian noise in slicer-2D coordinates and return (perturbed_point, delta)."""
        point = np.array(point2d, dtype=np.float64).flatten()[0:2]
        if float(sigma_px) <= 0.0:
            return point.copy(), np.zeros(2, dtype=np.float64)
        delta = np.random.normal(loc=0.0, scale=float(sigma_px), size=2)
        return point + delta, delta

    def _copy_marker_label_dict(self, marker_dict: dict) -> dict:
        """Copy a marker-label dictionary while normalizing point tuples to floats."""
        copied = {}
        for point, label in marker_dict.items():
            point_arr = np.array(point, dtype=np.float64).flatten()
            z_value = float(point_arr[2]) if point_arr.size > 2 else 0.0
            copied[(float(point_arr[0]), float(point_arr[1]), z_value)] = int(label)
        return copied

    def _snapshot_sorted_marker_views(self) -> dict:
        """Capture the current clean marker correspondences for all 3 views."""
        marker_views = {}
        for view_index in (1, 2, 3):
            big_markers = getattr(self, f"bigMarkersSort{view_index}", None)
            small_markers = getattr(self, f"smallMarkersSort{view_index}", None)
            if not isinstance(big_markers, dict) or not isinstance(small_markers, dict):
                raise ValueError(f"view{view_index} sorted marker correspondences are unavailable")
            marker_views[view_index] = {
                "big": self._copy_marker_label_dict(big_markers),
                "small": self._copy_marker_label_dict(small_markers),
            }
        return marker_views

    def _perturb_marker_label_dict(self, marker_dict: dict, sigma_px: float) -> dict:
        """Return a marker-label dictionary whose 2D points are perturbed but labels are preserved."""
        perturbed = {}
        for point, label in marker_dict.items():
            point_arr = np.array(point, dtype=np.float64).flatten()
            perturbed_xy, _ = self._perturb_slicer_2d_point(point_arr[0:2], sigma_px)
            z_value = float(point_arr[2]) if point_arr.size > 2 else 0.0
            perturbed[(float(perturbed_xy[0]), float(perturbed_xy[1]), z_value)] = int(label)
        return perturbed

    def _build_perturbed_marker_views(self, sigma_px: float) -> dict:
        """Return clean or perturbed marker 2D correspondences for each view."""
        marker_views = self._snapshot_sorted_marker_views()
        if float(sigma_px) <= 0.0:
            return marker_views
        perturbed_views = {}
        for view_index, view_data in marker_views.items():
            perturbed_views[view_index] = {
                "big": self._perturb_marker_label_dict(view_data["big"], sigma_px),
                "small": self._perturb_marker_label_dict(view_data["small"], sigma_px),
            }
        return perturbed_views

    def _get_active_perspective_calibrations(self):
        """Return the perturbation-adjusted perspective calibrations if a run is active."""
        if not self.controlledPerturbationEnabled:
            return self.perspectiveViewCalibs
        state = getattr(self, "controlledPerturbationRunState", None)
        if isinstance(state, dict) and isinstance(state.get("perspective_calibs"), dict):
            return state["perspective_calibs"]
        return self.perspectiveViewCalibs

    def _get_active_orthographic_calibrations(self):
        """Return the perturbation-adjusted orthographic calibrations if a run is active."""
        if not self.controlledPerturbationEnabled:
            return self.orthographicViewCalibs
        state = getattr(self, "controlledPerturbationRunState", None)
        if isinstance(state, dict) and isinstance(state.get("orthographic_calibs"), dict):
            return state["orthographic_calibs"]
        return self.orthographicViewCalibs

    def _prepare_controlled_perturbation_run_for_red(self, original_red_point2d: np.array):
        """Prepare one perturbation run context for the current redPush/greenPush sequence."""
        if not self.controlledPerturbationEnabled:
            self._reset_controlled_perturbation_run_state(clear_summary=True)
            self._update_controlled_perturbation_status()
            return None

        sigma_px = float(self.controlledPerturbationNoiseSigmaPx)
        noise_type = self.controlledPerturbationNoiseType
        self.controlledPerturbationRunCounter += 1
        run_id = self.controlledPerturbationRunCounter
        run_state = {
            "run_id": run_id,
            "noise_type": noise_type,
            "sigma_px": sigma_px,
            "stage": "prepared",
        }

        if noise_type == "marker-only":
            marker_views = self._build_perturbed_marker_views(sigma_px)
            if self.projectionMode == "perspective":
                run_state["perspective_calibs"] = self._solve_perspective_calibrations(marker_views)
            elif self.projectionMode == "orthographic":
                run_state["orthographic_calibs"] = self._solve_orthographic_calibrations(marker_views)
        else:
            perturbed_red_point, red_delta = self._perturb_slicer_2d_point(original_red_point2d, sigma_px)
            run_state["red_point_slicer"] = perturbed_red_point
            run_state["red_delta_slicer"] = red_delta
            self._set_controlled_perturbation_preview_point(
                "ControlledPerturbationRed2D",
                "vtkMRMLSliceNodeRed",
                perturbed_red_point,
                (0.85, 0.35, 0.1),
            )
            self._set_controlled_perturbation_preview_point(
                "ControlledPerturbationGreen2D",
                "vtkMRMLSliceNodeGreen",
                None,
                (0.1, 0.55, 0.85),
            )

        if noise_type != "target-only":
            self._set_controlled_perturbation_preview_point(
                "ControlledPerturbationRed2D",
                "vtkMRMLSliceNodeRed",
                None,
                (0.85, 0.35, 0.1),
            )
            self._set_controlled_perturbation_preview_point(
                "ControlledPerturbationGreen2D",
                "vtkMRMLSliceNodeGreen",
                None,
                (0.1, 0.55, 0.85),
            )

        self.controlledPerturbationRunState = run_state
        self.controlledPerturbationLastAppliedSummary = f"Run #{run_id} prepared"
        self._update_controlled_perturbation_status()
        return run_state

    def _require_controlled_perturbation_run_for_green(self):
        """Return the prepared perturbation run state required by greenPush, or report why it is unavailable."""
        if not self.controlledPerturbationEnabled:
            return None
        run_state = getattr(self, "controlledPerturbationRunState", None)
        if not isinstance(run_state, dict):
            self._error("Controlled perturbation is enabled. Please click redPush again to prepare a fresh perturbation run")
            return None
        if run_state.get("stage") == "completed":
            self._error("Please click redPush again to start a new controlled perturbation realization")
            return None
        return run_state

    def onControlledPerturbationOptionsChanged(self, *args) -> None:
        """Handle controlled perturbation UI option updates."""
        self._reset_controlled_perturbation_run_state(clear_summary=True)
        self._update_controlled_perturbation_status()

    def onRunControlledPerturbation(self, checked=False) -> None:
        """Toggle the controlled perturbation experiment workflow state."""
        self.controlledPerturbationEnabled = bool(checked)
        if hasattr(self.ui, "runControlledPerturbationButton") and self.ui.runControlledPerturbationButton is not None:
            self.ui.runControlledPerturbationButton.setChecked(self.controlledPerturbationEnabled)
        self._reset_controlled_perturbation_run_state(clear_summary=True)
        self._update_controlled_perturbation_status()
        logging.info(
            "Controlled perturbation toggled | enabled=%s | noise_type=%s | noise_sigma_px=%s",
            self.controlledPerturbationEnabled,
            self.controlledPerturbationNoiseType,
            self.controlledPerturbationNoiseSigmaPx,
        )

    def _run_controlled_perturbation_workflow_once(self, show_feedback: bool = True) -> None:
        """Run one CopyBlackCenter/redPush/greenPush workflow cycle."""
        previous_suppress_errors = self._suppressErrorDialogs
        try:
            if not show_feedback:
                self._suppressErrorDialogs = True
            if not self._require_markers_sorted():
                raise RuntimeError("markersSort prerequisites are not ready")

            for source_name in ("blackCenter1", "blackCenter2"):
                source_node = slicer.mrmlScene.GetFirstNodeByName(source_name)
                if source_node is None or source_node.GetNumberOfControlPoints() < 1:
                    if show_feedback:
                        self._error(f"Please click blackCenter first so {source_name} is available")
                    raise RuntimeError(f"{source_name} is not available")

            self.controlledPerturbationLastAppliedSummary = "Auto workflow running"
            self._update_controlled_perturbation_status()

            for stale_node_name in ("GreenLine2D", "TargetP3D", "TargetP2DYellow"):
                stale_node = slicer.mrmlScene.GetFirstNodeByName(stale_node_name)
                if stale_node is not None:
                    try:
                        slicer.mrmlScene.RemoveNode(stale_node)
                    except Exception:
                        logging.warning("Failed to remove stale %s before auto workflow", stale_node_name, exc_info=True)

            self.onCopyBlackCenter1()
            point_red = slicer.mrmlScene.GetFirstNodeByName("PointRed")
            if point_red is None or point_red.GetNumberOfControlPoints() < 1:
                raise RuntimeError("PointRed was not created from blackCenter1")
            self._set_selector_current_node(self.ui.Red2DPSelector, point_red)

            self.onTwoD2ThreeDRed()

            point_green_line = slicer.mrmlScene.GetFirstNodeByName("GreenLine2D")
            if point_green_line is None:
                raise RuntimeError("redPush did not generate GreenLine2D")

            self.onCopyBlackCenter2()
            point_green = slicer.mrmlScene.GetFirstNodeByName("PointGreen")
            if point_green is None or point_green.GetNumberOfControlPoints() < 1:
                raise RuntimeError("PointGreen was not created from blackCenter2")
            self._set_selector_current_node(self.ui.Green2DPSelector, point_green)

            self.onTwoD2ThreeDGreen()
            target_p3d = slicer.mrmlScene.GetFirstNodeByName("TargetP3D")
            target_p2d_yellow = slicer.mrmlScene.GetFirstNodeByName("TargetP2DYellow")
            if target_p3d is None or target_p2d_yellow is None:
                raise RuntimeError("greenPush did not generate the expected TargetP3D / TargetP2DYellow results")
            self.onCalculateTRE()
            self.onCalculateReprojectionError()
        except Exception as exc:
            self.controlledPerturbationLastAppliedSummary = "Auto workflow failed"
            self._update_controlled_perturbation_status()
            if show_feedback:
                self._error("Run Full Copy+Push Workflow failed", detailedText=str(exc))
            raise
        else:
            run_state = self.controlledPerturbationRunState if isinstance(self.controlledPerturbationRunState, dict) else None
            if run_state is not None and run_state.get("stage") == "completed":
                self.controlledPerturbationLastAppliedSummary = f"Run #{run_state['run_id']} completed | Auto workflow"
            else:
                self.controlledPerturbationLastAppliedSummary = "Auto workflow completed"
            self._update_controlled_perturbation_status()
        finally:
            self._suppressErrorDialogs = previous_suppress_errors

    def onRunControlledPerturbationWorkflow(self, checked=False) -> None:
        """Run the repeated CopyBlackCenter/redPush/greenPush workflow in one click."""
        try:
            self._run_controlled_perturbation_workflow_once(show_feedback=True)
        except Exception:
            return

    def _get_controlled_perturbation_batch_count(self) -> int:
        """Return the configured batch repeat count for auto workflow export loops."""
        spin_box = getattr(self.ui, "controlledPerturbationBatchCountSpinBox", None)
        if spin_box is None or not hasattr(spin_box, "value"):
            return 1
        try:
            return max(1, int(spin_box.value))
        except TypeError:
            return max(1, int(spin_box.value()))

    def _get_controlled_perturbation_batch_max_attempts(self, target_success_count: int) -> int:
        """Return a safety cap on batch attempts to avoid infinite retries when many samples fail."""
        target_success_count = max(1, int(target_success_count))
        return min(max(target_success_count * 10, target_success_count + 20), 5000)

    def _is_fatal_batch_workflow_error(self, exc: Exception) -> bool:
        """Return whether a workflow exception indicates a broken setup rather than a skip-worthy sample."""
        message = str(exc)
        fatal_markers = (
            "markersSort prerequisites are not ready",
            "blackCenter1 is not available",
            "blackCenter2 is not available",
            "PointRed was not created from blackCenter1",
            "PointGreen was not created from blackCenter2",
        )
        return any(marker in message for marker in fatal_markers)

    def onRunControlledPerturbationBatchWorkflow(self, checked=False) -> None:
        """Collect the requested number of successful Auto Copy+Push + CSV exports, skipping failed samples."""
        target_success_count = self._get_controlled_perturbation_batch_count()
        if target_success_count < 1:
            self._error("Batch count must be at least 1")
            return
        if not self._require_markers_sorted():
            return
        for source_name in ("blackCenter1", "blackCenter2"):
            source_node = slicer.mrmlScene.GetFirstNodeByName(source_name)
            if source_node is None or source_node.GetNumberOfControlPoints() < 1:
                self._error(f"Please click blackCenter first so {source_name} is available")
                return

        completed_count = 0
        skipped_count = 0
        attempt_count = 0
        max_attempts = self._get_controlled_perturbation_batch_max_attempts(target_success_count)
        csv_path = getattr(self, "csvFilePath", "") or self._default_experiment_csv_path()
        batch_start = time.perf_counter()

        try:
            while completed_count < target_success_count:
                if attempt_count >= max_attempts:
                    raise RuntimeError(
                        f"Reached batch retry safety cap ({max_attempts} attempts) before collecting "
                        f"{target_success_count} successful runs"
                    )

                attempt_count += 1
                self.controlledPerturbationLastAppliedSummary = (
                    f"Batch {completed_count}/{target_success_count} saved | "
                    f"skipped {skipped_count} | attempt {attempt_count}"
                )
                self._update_controlled_perturbation_status()
                slicer.app.processEvents()

                try:
                    self._run_controlled_perturbation_workflow_once(show_feedback=False)
                except Exception as exc:
                    if self._is_fatal_batch_workflow_error(exc):
                        raise
                    skipped_count += 1
                    logging.warning(
                        "Skipped failed perturbation sample during batch run | attempt=%s | skipped=%s | error=%s",
                        attempt_count,
                        skipped_count,
                        exc,
                    )
                    self.controlledPerturbationLastAppliedSummary = (
                        f"Batch {completed_count}/{target_success_count} saved | "
                        f"skipped {skipped_count} | last skip at attempt {attempt_count}"
                    )
                    self._update_controlled_perturbation_status()
                    slicer.app.processEvents()
                    continue

                csv_path = self._save_current_results_csv(show_feedback=False)
                completed_count += 1

                self.controlledPerturbationLastAppliedSummary = (
                    f"Batch {completed_count}/{target_success_count} saved | "
                    f"skipped {skipped_count} | attempts {attempt_count}"
                )
                self._update_controlled_perturbation_status()
                slicer.app.processEvents()
        except Exception as exc:
            self.controlledPerturbationLastAppliedSummary = (
                f"Batch stopped: {completed_count}/{target_success_count} saved | "
                f"skipped {skipped_count} | attempts {attempt_count}"
            )
            self._update_controlled_perturbation_status()
            self._error(
                "Batch Auto Copy+Push + CSV export stopped before reaching the requested success count",
                detailedText=str(exc),
            )
            return

        elapsed_seconds = time.perf_counter() - batch_start
        self.controlledPerturbationLastAppliedSummary = (
            f"Batch completed: {completed_count}/{target_success_count} saved | "
            f"skipped {skipped_count} | attempts {attempt_count}"
        )
        self._update_controlled_perturbation_status()
        logging.info(
            "Completed Auto Copy+Push + CSV batch | success_count=%s | skipped_count=%s | attempts=%s | "
            "csv_path=%s | elapsed_seconds=%.3f",
            completed_count,
            skipped_count,
            attempt_count,
            csv_path,
            elapsed_seconds,
        )
        try:
            slicer.util.infoDisplay(
                "Batch Auto Copy+Push + CSV export completed:\n"
                f"Successful runs: {completed_count}/{target_success_count}\n"
                f"Skipped failed attempts: {skipped_count}\n"
                f"Total attempts: {attempt_count}\n"
                f"Saved to:\n{csv_path}\n"
                f"Elapsed: {elapsed_seconds:.1f} s"
            )
        except Exception:
            pass

    def _selector_current_node_name(self, selector_name: str) -> str:
        """Return current node name from a qMRMLNodeComboBox-like widget."""
        selector = getattr(self.ui, selector_name, None)
        if selector is None or not hasattr(selector, "currentNode"):
            return ""
        node = selector.currentNode()
        return node.GetName() if node else ""

    def _extract_first_float(self, text_value):
        """Extract first float from text like '12.34 mm'; return empty string if absent."""
        if text_value is None:
            return ""
        match = re.search(r"-?\d+(?:\.\d+)?", str(text_value))
        if not match:
            return ""
        return float(match.group(0))

    def _get_first_control_point_xyz(self, node_name: str):
        """Return first control point xyz (world coordinates) by node name, or empty values."""
        node = slicer.mrmlScene.GetFirstNodeByName(node_name)
        if node is None or not hasattr(node, "GetNumberOfControlPoints"):
            return "", "", ""
        if node.GetNumberOfControlPoints() < 1:
            return "", "", ""
        if hasattr(node, "GetNthControlPointPositionWorld"):
            pos = node.GetNthControlPointPositionWorld(0)
        else:
            pos = node.GetNthControlPointPosition(0)
        return float(pos[0]), float(pos[1]), float(pos[2])

    def _ensure_testpoint_node(self):
        """Return the scene testPoint node, creating it if needed."""
        testpoint_node = slicer.mrmlScene.GetFirstNodeByName("testPoint")
        if testpoint_node is None:
            testpoint_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode",
                "testPoint",
            )
            testpoint_node.CreateDefaultDisplayNodes()
        display_node = testpoint_node.GetDisplayNode()
        if display_node:
            display_node.SetPointLabelsVisibility(False)
            display_node.SetSelectedColor(0, 0, 0)
            display_node.SetColor(0, 0, 0)
            display_node.SetVisibility(True)
            if hasattr(display_node, "SetVisibility3D"):
                display_node.SetVisibility3D(True)
            if hasattr(display_node, "SetVisibility2D"):
                display_node.SetVisibility2D(False)
        return testpoint_node

    def _set_testpoint_position(self, position):
        """Set the scene testPoint node to the provided world-space position."""
        testpoint_node = self._ensure_testpoint_node()
        if testpoint_node.GetNumberOfControlPoints() < 1:
            testpoint_node.AddControlPoint(position)
        else:
            testpoint_node.SetNthControlPointPosition(0, position)
        return testpoint_node

    def _run_timed_step(self, step_name: str, callback, *args, **kwargs):
        """Run callback and store elapsed wall time in milliseconds."""
        start_time = time.perf_counter()
        try:
            return callback(*args, **kwargs)
        finally:
            self.stepTimingsMs[step_name] = round((time.perf_counter() - start_time) * 1000.0, 3)

    def _marker_sort_metric(self, view_index: int, key: str):
        """Return cached marker-sorting metric for a given view, or empty string."""
        metrics = self.markerSortMetrics.get(view_index, {})
        return metrics.get(key, "")

    def _csv_value_or_na(self, value):
        """Return CSV-friendly NA sentinel for missing metric values."""
        if value is None:
            return "NA"
        if isinstance(value, str) and value == "":
            return "NA"
        return value

    def _safe_filename_component(self, value: str) -> str:
        """Convert arbitrary text to a filesystem-friendly filename component."""
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
        sanitized = sanitized.strip("._-")
        return sanitized or "item"

    def _parse_optional_float(self, value):
        """Return float(value) when available, otherwise None."""
        text_value = self._normalize_optional_csv_value(value)
        if text_value is None:
            return None
        try:
            return float(text_value)
        except (TypeError, ValueError):
            return None

    def _format_condition_id_component(self, value, digits: int = 2) -> str:
        """Format numeric metadata into a stable key component used by exported experiment IDs."""
        float_value = self._parse_optional_float(value)
        if float_value is not None:
            return f"{float_value:.{digits}f}"
        text_value = self._normalize_optional_csv_value(value)
        if text_value is None:
            return "na"
        return self._safe_filename_component(text_value)

    def _build_point_id(self, testpoint_x, testpoint_y, testpoint_z, digits: int = 2) -> str:
        """Build a stable point identifier from testPoint world coordinates."""
        return "tp_" + "_".join(
            self._format_condition_id_component(value, digits)
            for value in (testpoint_x, testpoint_y, testpoint_z)
        )

    def _build_base_condition_id(
        self,
        dataset_name: str,
        projection_mode: str,
        testpoint_xyz,
        shot_angles_deg,
        digits: int = 2,
    ) -> str:
        """Build a stable base-condition identifier from volume, projection, testPoint, and angle metadata."""
        testpoint_x, testpoint_y, testpoint_z = testpoint_xyz
        shot2_angle_deg, shot3_angle_m3_m1_deg, shot3_angle_m3_m2_deg = shot_angles_deg
        dataset_key = self._safe_filename_component(dataset_name or "unknown_volume")
        projection_key = self._safe_filename_component(projection_mode or "unknown_projection")
        point_key = self._build_point_id(testpoint_x, testpoint_y, testpoint_z, digits)
        angle_key = "__".join(
            [
                f"shot2_{self._format_condition_id_component(shot2_angle_deg, digits)}",
                f"shot3m3m1_{self._format_condition_id_component(shot3_angle_m3_m1_deg, digits)}",
                f"shot3m3m2_{self._format_condition_id_component(shot3_angle_m3_m2_deg, digits)}",
            ]
        )
        return f"{dataset_key}__{projection_key}__{point_key}__{angle_key}"

    def _normalize_experiment_noise_metadata(self, noise_type, sigma_px):
        """Normalize perturbation metadata for experiment-table export."""
        normalized_noise_type = self._normalize_optional_csv_value(noise_type) or ""
        normalized_sigma = self._parse_optional_float(sigma_px)
        is_clean_baseline = 0
        if normalized_sigma is not None and abs(normalized_sigma) <= 1e-9:
            is_clean_baseline = 1
        elif normalized_noise_type == "baseline":
            is_clean_baseline = 1

        if is_clean_baseline:
            normalized_noise_type = "baseline"
            normalized_sigma = 0.0

        return normalized_noise_type, normalized_sigma, is_clean_baseline

    def _normalized_experiment_noise_from_row(self, row: dict):
        """Read normalized noise metadata from either the new schema or legacy perturbation fields."""
        noise_type = row.get("noise_type", "")
        sigma_px = row.get("noise_sigma_px", "")
        if self._normalize_optional_csv_value(noise_type) is None:
            noise_type = row.get("controlled_perturbation_noise_type", "")
        if self._normalize_optional_csv_value(sigma_px) is None:
            sigma_px = row.get("controlled_perturbation_noise_sigma_px", "")
        return self._normalize_experiment_noise_metadata(noise_type, sigma_px)

    def _build_base_condition_id_from_row(self, row: dict, digits: int = 2) -> str:
        """Reconstruct base_condition_id from an exported CSV row, including legacy rows without this field."""
        existing_value = self._normalize_optional_csv_value(row.get("base_condition_id", ""))
        if existing_value is not None:
            return existing_value
        dataset_name = (
            self._normalize_optional_csv_value(row.get("dataset", ""))
            or self._normalize_optional_csv_value(row.get("input_volume", ""))
            or "unknown_volume"
        )
        projection_mode = self._normalize_optional_csv_value(row.get("projection_mode", "")) or "unknown_projection"
        testpoint_xyz = (
            row.get("testpoint_x", ""),
            row.get("testpoint_y", ""),
            row.get("testpoint_z", ""),
        )
        shot_angles_deg = (
            row.get("shot2_angle_deg", ""),
            row.get("shot3_angle_m3_m1_deg", ""),
            row.get("shot3_angle_m3_m2_deg", ""),
        )
        return self._build_base_condition_id(
            dataset_name,
            projection_mode,
            testpoint_xyz,
            shot_angles_deg,
            digits=digits,
        )

    def _count_existing_experiment_repeats(self, csv_path: str, base_condition_id: str, noise_type: str, noise_sigma_px) -> int:
        """Count how many rows already exist for the same base condition and normalized noise setting."""
        if not csv_path or not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
            return 0
        target_noise_type = self._normalize_optional_csv_value(noise_type) or ""
        target_sigma = self._parse_optional_float(noise_sigma_px)
        repeat_count = 0
        with open(csv_path, "r", newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            for existing_row in reader:
                if self._build_base_condition_id_from_row(existing_row) != base_condition_id:
                    continue
                existing_noise_type, existing_sigma, _ = self._normalized_experiment_noise_from_row(existing_row)
                if existing_noise_type != target_noise_type:
                    continue
                if target_sigma is None and existing_sigma is None:
                    repeat_count += 1
                elif target_sigma is not None and existing_sigma is not None and abs(existing_sigma - target_sigma) <= 1e-9:
                    repeat_count += 1
        return repeat_count

    def _mean_numeric_values(self, values):
        """Return mean of available numeric values, or empty string if none are valid."""
        numeric_values = []
        for value in values:
            float_value = self._parse_optional_float(value)
            if float_value is not None:
                numeric_values.append(float_value)
        return float(np.mean(numeric_values)) if numeric_values else ""

    def _export_marker_transform_snapshots(self, csv_path: str, record_id: str):
        """Save the 3 marker transform nodes next to the CSV for experiment reproducibility."""
        transform_names = ("LinearTransform", "LinearTransform_1", "LinearTransform_2")
        csv_dir = os.path.dirname(csv_path) or getattr(self, "experimentPath", self.savePath)
        export_root = os.path.join(csv_dir, "transform_snapshots")
        export_dir = os.path.join(export_root, record_id)
        os.makedirs(export_dir, exist_ok=True)

        export_info = {
            "experiment_record_id": record_id,
            "marker_transform_snapshot_dir": export_dir,
        }
        saved_count = 0
        for idx, transform_name in enumerate(transform_names, start=1):
            prefix = f"marker_transform_{idx}"
            export_info[f"{prefix}_name"] = transform_name
            export_info[f"{prefix}_path"] = "NA"
            export_info[f"{prefix}_saved"] = 0

            transform_node = slicer.mrmlScene.GetFirstNodeByName(transform_name)
            if transform_node is None:
                continue

            filename = (
                f"{record_id}_{idx}_{self._safe_filename_component(transform_name)}.h5"
            )
            output_path = os.path.join(export_dir, filename)
            try:
                save_ok = bool(slicer.util.saveNode(transform_node, output_path))
            except Exception:
                logging.exception("Failed to save transform snapshot: %s", transform_name)
                save_ok = False

            if not save_ok:
                continue

            export_info[f"{prefix}_path"] = output_path
            export_info[f"{prefix}_saved"] = 1
            saved_count += 1

        export_info["marker_transform_snapshot_count"] = saved_count
        export_info["marker_transform_snapshot_ready"] = int(saved_count == len(transform_names))
        if saved_count == 0:
            try:
                if not os.listdir(export_dir):
                    os.rmdir(export_dir)
            except OSError:
                pass
            export_info["marker_transform_snapshot_dir"] = "NA"
        return export_info

    def _append_csv_row_with_schema_upgrade(self, csv_path: str, row: dict) -> None:
        """Append a row to CSV and upgrade the file header if new columns were introduced."""
        field_names = list(row.keys())
        if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
            with open(csv_path, "r", newline="", encoding="utf-8-sig") as csv_file:
                reader = csv.DictReader(csv_file)
                existing_field_names = reader.fieldnames or []
                if existing_field_names != field_names:
                    existing_rows = list(reader)
                    merged_field_names = list(field_names)
                    for field_name in existing_field_names:
                        if field_name not in merged_field_names:
                            merged_field_names.append(field_name)
                    for existing_row in existing_rows:
                        for field_name in merged_field_names:
                            existing_row.setdefault(field_name, "NA")
                    for field_name in merged_field_names:
                        row.setdefault(field_name, "NA")
                    with open(csv_path, "w", newline="", encoding="utf-8-sig") as rewrite_file:
                        writer = csv.DictWriter(rewrite_file, fieldnames=merged_field_names)
                        writer.writeheader()
                        writer.writerows(existing_rows)
                    field_names = merged_field_names
                else:
                    for field_name in field_names:
                        row.setdefault(field_name, "NA")

        write_header = (not os.path.exists(csv_path)) or os.path.getsize(csv_path) == 0
        with open(csv_path, "a", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=field_names)
            if write_header:
                writer.writeheader()
            writer.writerow(row)

    def _selector_matches_named_node(self, selector_name: str, target_node_name: str, tolerance: float = 1e-6) -> bool:
        """Check whether selector current node first control point matches the target node."""
        selector = getattr(self.ui, selector_name, None)
        if selector is None or not hasattr(selector, "currentNode"):
            return False
        selected_node = selector.currentNode()
        target_node = slicer.mrmlScene.GetFirstNodeByName(target_node_name)
        if selected_node is None or target_node is None:
            return False
        if not hasattr(selected_node, "GetNumberOfControlPoints") or not hasattr(target_node, "GetNumberOfControlPoints"):
            return False
        if selected_node.GetNumberOfControlPoints() < 1 or target_node.GetNumberOfControlPoints() < 1:
            return False
        selected_pos = np.array(selected_node.GetNthControlPointPosition(0), dtype=float)
        target_pos = np.array(target_node.GetNthControlPointPosition(0), dtype=float)
        return bool(np.linalg.norm(selected_pos - target_pos) <= tolerance)

    def _marker_distribution_center_for_transform(self, transform_name: str):
        """Return marker distribution center in world space for a transform, or None."""
        if not hasattr(self, "generateMarkers") or self.generateMarkers is None:
            return None
        transform_node = slicer.mrmlScene.GetFirstNodeByName(transform_name)
        if transform_node is None:
            return None
        big_markers = self.generateMarkers.getMarkerTransform(
            transform_node,
            self.generateMarkers.bigMarker3DDic,
        )
        small_markers = self.generateMarkers.getMarkerTransform(
            transform_node,
            self.generateMarkers.smallMarker3DDic,
        )
        points = [np.array(p, dtype=float) for p in big_markers.values()]
        points.extend(np.array(p, dtype=float) for p in small_markers.values())
        if not points:
            return None
        return np.mean(np.vstack(points), axis=0)

    def _testpoint_marker_distances_mm(self):
        """Return distances from testPoint to marker centers of 3 transforms and their mean."""
        testpoint_xyz = self._get_first_control_point_xyz("testPoint")
        if "" in testpoint_xyz:
            return "", "", "", ""

        testpoint = np.array(testpoint_xyz, dtype=float)
        transform_names = ("LinearTransform", "LinearTransform_1", "LinearTransform_2")
        distances = []
        for transform_name in transform_names:
            center = self._marker_distribution_center_for_transform(transform_name)
            if center is None:
                distances.append("")
            else:
                distances.append(float(np.linalg.norm(testpoint - center)))

        valid_distances = [d for d in distances if d != ""]
        mean_distance = float(np.mean(valid_distances)) if len(valid_distances) == 3 else ""
        return distances[0], distances[1], distances[2], mean_distance

    def _is_calibration_ready(self, calibs) -> bool:
        """Check whether per-view calibration dict contains 3 views."""
        return isinstance(calibs, dict) and all(view_idx in calibs for view_idx in (1, 2, 3))

    def _default_experiment_csv_path(self) -> str:
        """Return the default CSV path used for experiment import/export workflows."""
        return os.path.join(getattr(self, "experimentPath", self.savePath), "experiment_results.csv")

    def _set_csv_file_path(self, path_text: str) -> None:
        """Normalize and apply export CSV path from UI or file dialog."""
        if not path_text:
            return
        normalized = os.path.normpath(str(path_text).strip())
        if not normalized:
            return
        if not normalized.lower().endswith(".csv"):
            normalized = f"{normalized}.csv"
        self.csvFilePath = normalized
        if hasattr(self.ui, "csvSavePathLineEdit") and self.ui.csvSavePathLineEdit is not None:
            self.ui.csvSavePathLineEdit.setText(self.csvFilePath)

    def _set_import_csv_file_path(self, path_text: str) -> None:
        """Normalize and apply imported CSV path from UI or file dialog."""
        if not path_text:
            return
        normalized = os.path.normpath(str(path_text).strip())
        self.importCsvFilePath = normalized
        if hasattr(self.ui, "importCsvPathLineEdit") and self.ui.importCsvPathLineEdit is not None:
            self.ui.importCsvPathLineEdit.setText(self.importCsvFilePath)

    def _update_import_csv_status(self) -> None:
        """Refresh import-row status label and row navigation button states."""
        total_rows = len(self.importedExperimentRows)
        selected_row = self.importedExperimentRowIndex + 1 if 0 <= self.importedExperimentRowIndex < total_rows else 0
        status_text = f"Rows: {total_rows} | Selected: {selected_row}"
        if getattr(self, "importedExperimentRestoreStatus", ""):
            status_text = f"{status_text} | {self.importedExperimentRestoreStatus}"
        if hasattr(self.ui, "importCsvStatusLabel") and self.ui.importCsvStatusLabel is not None:
            self.ui.importCsvStatusLabel.setText(status_text)

        prev_enabled = total_rows > 0 and self.importedExperimentRowIndex > 0
        next_enabled = total_rows > 0 and self.importedExperimentRowIndex < total_rows - 1
        if hasattr(self.ui, "importCsvPrevRowButton") and self.ui.importCsvPrevRowButton is not None:
            self.ui.importCsvPrevRowButton.setEnabled(prev_enabled)
        if hasattr(self.ui, "importCsvNextRowButton") and self.ui.importCsvNextRowButton is not None:
            self.ui.importCsvNextRowButton.setEnabled(next_enabled)

        jump_enabled = total_rows > 0
        if self.importCsvJumpRowValidator is not None:
            self.importCsvJumpRowValidator.setRange(1, max(total_rows, 1))
        if hasattr(self.ui, "importCsvJumpRowLineEdit") and self.ui.importCsvJumpRowLineEdit is not None:
            desired_text = str(selected_row) if selected_row > 0 else ""
            current_text = self._widget_text("importCsvJumpRowLineEdit")
            self.ui.importCsvJumpRowLineEdit.setEnabled(jump_enabled)
            self.ui.importCsvJumpRowLineEdit.setPlaceholderText(
                f"1-{total_rows}" if jump_enabled else "Load CSV first"
            )
            if current_text != desired_text:
                self.ui.importCsvJumpRowLineEdit.setText(desired_text)
        if hasattr(self.ui, "importCsvJumpRowButton") and self.ui.importCsvJumpRowButton is not None:
            self.ui.importCsvJumpRowButton.setEnabled(jump_enabled)

    def _parse_imported_testpoint_xyz(self, row: dict):
        """Return imported testPoint xyz as floats, or None if the row is invalid."""
        coords = []
        for field_name in ("testpoint_x", "testpoint_y", "testpoint_z"):
            raw_value = row.get(field_name, "")
            text_value = self._normalize_optional_csv_value(raw_value)
            if text_value is None:
                return None
            try:
                coords.append(float(text_value))
            except ValueError:
                return None
        return tuple(coords)

    def _normalize_optional_csv_value(self, value):
        """Normalize optional CSV cell content and map empty/NA values to None."""
        if value is None:
            return None
        text_value = str(value).strip()
        if not text_value or text_value.upper() == "NA":
            return None
        return text_value

    def _restore_transform_snapshots_from_row(self, row_index: int):
        """Restore the 3 saved marker transform snapshots for the selected row."""
        if row_index < 0 or row_index >= len(self.importedExperimentRows):
            return False, "Transforms: n/a"

        row = self.importedExperimentRows[row_index]
        transform_specs = []
        missing_paths = []
        for idx, target_name in enumerate(("LinearTransform", "LinearTransform_1", "LinearTransform_2"), start=1):
            path_value = self._normalize_optional_csv_value(row.get(f"marker_transform_{idx}_path", ""))
            if path_value is None:
                missing_paths.append(idx)
                continue
            transform_specs.append((idx, target_name, os.path.normpath(path_value)))

        if missing_paths:
            return False, "Transforms: unavailable"

        missing_files = [str(idx) for idx, _, path in transform_specs if not os.path.exists(path)]
        if missing_files:
            return False, f"Transforms: missing files ({','.join(missing_files)}/3)"

        self._ensureLinearTransformNodes()
        loaded_nodes = []
        try:
            for _, _, transform_path in transform_specs:
                loaded_node = slicer.util.loadTransform(transform_path)
                if isinstance(loaded_node, (list, tuple)):
                    loaded_node = loaded_node[0] if loaded_node else None
                if loaded_node is None:
                    raise RuntimeError(f"Failed to load transform file: {transform_path}")
                loaded_nodes.append(loaded_node)

            for (_, target_name, _), loaded_node in zip(transform_specs, loaded_nodes):
                target_node = slicer.mrmlScene.GetFirstNodeByName(target_name)
                if target_node is None:
                    raise RuntimeError(f"Scene transform node not found: {target_name}")
                transform_matrix = vtk.vtkMatrix4x4()
                loaded_node.GetMatrixTransformToParent(transform_matrix)
                target_node.SetAndObserveMatrixTransformToParent(transform_matrix)
            self._observe_transform_nodes_for_angles()
            if self._markersSorted:
                self._update_shot2_angle_display()
                self._update_shot3_angle_display()
            return True, "Transforms: 3/3 restored"
        except Exception as exc:
            logging.exception("Failed to restore transform snapshots from imported CSV row")
            return False, f"Transforms: restore failed"
        finally:
            for loaded_node in loaded_nodes:
                try:
                    slicer.mrmlScene.RemoveNode(loaded_node)
                except Exception:
                    pass

    def _apply_imported_row_to_testpoint(self, row_index: int) -> bool:
        """Apply imported row testPoint coordinates into the current scene."""
        if row_index < 0 or row_index >= len(self.importedExperimentRows):
            return False
        coords = self._parse_imported_testpoint_xyz(self.importedExperimentRows[row_index])
        if coords is None:
            self._error("Selected CSV row does not contain a valid testPoint position")
            return False
        self._set_testpoint_position(coords)
        return True

    def _select_imported_experiment_row(self, row_index: int) -> None:
        """Select an imported CSV row and apply its testPoint to the scene."""
        total_rows = len(self.importedExperimentRows)
        if total_rows == 0:
            self.importedExperimentRowIndex = -1
            self.importedExperimentRestoreStatus = "Transforms: n/a"
            self._update_import_csv_status()
            return
        row_index = max(0, min(row_index, total_rows - 1))
        self.importedExperimentRowIndex = row_index
        self._apply_imported_row_to_testpoint(row_index)
        _, restore_status = self._restore_transform_snapshots_from_row(row_index)
        self.importedExperimentRestoreStatus = restore_status
        self._update_import_csv_status()

    def onBrowseImportCsvPath(self):
        """Open file dialog to choose an experiment CSV for import."""
        current_path = getattr(self, "importCsvFilePath", "") or self._default_experiment_csv_path()
        selected_path = qt.QFileDialog.getOpenFileName(
            slicer.util.mainWindow(),
            "Select Experiment CSV to Import",
            current_path,
            "CSV Files (*.csv)",
        )
        if not selected_path:
            return
        self._set_import_csv_file_path(selected_path)

    def onLoadImportCsv(self):
        """Load experiment rows from the selected CSV file."""
        csv_path = self._widget_text("importCsvPathLineEdit") or getattr(self, "importCsvFilePath", "")
        if not csv_path:
            self._error("Please select an experiment CSV file first")
            return
        csv_path = os.path.normpath(csv_path)
        if not os.path.exists(csv_path):
            self._error(f"CSV file not found: {csv_path}")
            return

        try:
            with open(csv_path, "r", newline="", encoding="utf-8-sig") as csv_file:
                reader = csv.DictReader(csv_file)
                self.importedExperimentFieldNames = reader.fieldnames or []
                self.importedExperimentRows = [
                    row
                    for row in reader
                    if any(str(value).strip() for value in row.values() if value is not None)
                ]
        except Exception as exc:
            self._error(f"Failed to load experiment CSV: {exc}")
            return

        self._set_import_csv_file_path(csv_path)
        if not self.importedExperimentRows:
            self.importedExperimentRowIndex = -1
            self.importedExperimentRestoreStatus = "Transforms: n/a"
            self._update_import_csv_status()
            self._error("Selected CSV file does not contain any data rows")
            return
        self._select_imported_experiment_row(0)

    def onImportCsvPreviousRow(self):
        """Select the previous imported CSV row."""
        if not self.importedExperimentRows:
            self._error("Please load an experiment CSV first")
            return
        self._select_imported_experiment_row(self.importedExperimentRowIndex - 1)

    def onImportCsvNextRow(self):
        """Select the next imported CSV row."""
        if not self.importedExperimentRows:
            self._error("Please load an experiment CSV first")
            return
        self._select_imported_experiment_row(self.importedExperimentRowIndex + 1)

    def onImportCsvJumpToRow(self):
        """Jump directly to a 1-based imported CSV row number from the UI input."""
        if not self.importedExperimentRows:
            self._error("Please load an experiment CSV first")
            return

        row_text = self._widget_text("importCsvJumpRowLineEdit")
        if not row_text:
            self._error("Please enter a row number to jump to")
            return

        try:
            row_number = int(row_text)
        except ValueError:
            self._error("Please enter a valid row number")
            return

        total_rows = len(self.importedExperimentRows)
        if row_number < 1 or row_number > total_rows:
            self._error(f"Please enter a row number between 1 and {total_rows}")
            return

        self._select_imported_experiment_row(row_number - 1)

    def onBrowseCsvPath(self):
        """Open file dialog to choose export CSV output path."""
        current_path = getattr(self, "csvFilePath", "") or self._default_experiment_csv_path()
        selected_path = qt.QFileDialog.getSaveFileName(
            slicer.util.mainWindow(),
            "Select Export CSV Path",
            current_path,
            "CSV Files (*.csv)",
        )
        if not selected_path:
            return
        self._set_csv_file_path(selected_path)

    def _save_current_results_csv(self, show_feedback: bool = True) -> str:
        """Append current test outputs and key runtime status fields into the export CSV."""
        try:
            if self._markersSorted:
                self._update_shot2_angle_display()
                self._update_shot3_angle_display()

            manual_csv_path = self._widget_text("csvSavePathLineEdit")
            if manual_csv_path:
                self._set_csv_file_path(manual_csv_path)
            csv_path = getattr(self, "csvFilePath", "") or self._default_experiment_csv_path()
            csv_dir = os.path.dirname(csv_path)
            if csv_dir:
                os.makedirs(csv_dir, exist_ok=True)
            capture_time = datetime.now().astimezone()
            record_id = capture_time.strftime("%Y%m%d_%H%M%S_%f")
            transform_snapshot_info = self._export_marker_transform_snapshots(csv_path, record_id)

            tre_display = self._widget_text("treValueDisplay")
            reproj_display = self._widget_text("reprojectionValueDisplay")
            line_gap_display = self._widget_text("lineGapDisplay")
            shot2_angle_display = self._widget_text("shot2AngleLineEdit")
            shot3_angle1_display = self._widget_text("shot3Angle1LineEdit")
            shot3_angle2_display = self._widget_text("shot3Angle2LineEdit")
            shot2_angle_deg = self._extract_first_float(shot2_angle_display)
            shot3_angle_m3_m1_deg = self._extract_first_float(shot3_angle1_display)
            shot3_angle_m3_m2_deg = self._extract_first_float(shot3_angle2_display)
            testpoint_x, testpoint_y, testpoint_z = self._get_first_control_point_xyz("testPoint")
            (
                testpoint_marker_distance_t1_mm,
                testpoint_marker_distance_t2_mm,
                testpoint_marker_distance_t3_mm,
                testpoint_marker_distance_mean_mm,
            ) = self._testpoint_marker_distances_mm()

            input_volume_node = self._getBodyVolumeNode()
            input_volume_name = input_volume_node.GetName() if input_volume_node else ""
            camera_view_angle_deg = self._get_current_3d_camera_view_angle()
            red_uses_blackcenter_auto_point = int(self._selector_matches_named_node("Red2DPSelector", "blackCenter1"))
            green_uses_blackcenter_auto_point = int(self._selector_matches_named_node("Green2DPSelector", "blackCenter2"))
            tre_value_mm = self.lastTreMmRaw if self.lastTreMmRaw is not None else self._extract_first_float(tre_display)
            reprojection_error_px = (
                self.lastReprojectionErrorPxRaw
                if self.lastReprojectionErrorPxRaw is not None
                else self._extract_first_float(reproj_display)
            )
            ray_gap_mm = self.lastRayGapMmRaw if self.lastRayGapMmRaw is not None else self._extract_first_float(line_gap_display)
            perturbation_state = (
                self.controlledPerturbationRunState
                if isinstance(self.controlledPerturbationRunState, dict)
                else {}
            )
            raw_noise_type = perturbation_state.get(
                "noise_type",
                self.controlledPerturbationNoiseType if self.controlledPerturbationEnabled else "",
            )
            raw_noise_sigma_px = perturbation_state.get(
                "sigma_px",
                float(self.controlledPerturbationNoiseSigmaPx) if self.controlledPerturbationEnabled else "",
            )
            (
                normalized_noise_type,
                normalized_noise_sigma_px,
                is_clean_baseline,
            ) = self._normalize_experiment_noise_metadata(raw_noise_type, raw_noise_sigma_px)
            experiment_family = (
                "controlled_perturbation_study"
                if (
                    bool(perturbation_state)
                    or bool(self.controlledPerturbationEnabled)
                    or normalized_noise_type != ""
                    or normalized_noise_sigma_px is not None
                )
                else "standard_reconstruction"
            )
            point_id = self._build_point_id(testpoint_x, testpoint_y, testpoint_z)
            base_condition_id = self._build_base_condition_id(
                input_volume_name,
                self.projectionMode,
                (testpoint_x, testpoint_y, testpoint_z),
                (shot2_angle_deg, shot3_angle_m3_m1_deg, shot3_angle_m3_m2_deg),
            )
            perspective_calibration_ready = int(self._is_calibration_ready(self._get_active_perspective_calibrations()))
            orthographic_calibration_ready = int(self._is_calibration_ready(self._get_active_orthographic_calibrations()))
            row = {
                "timestamp": capture_time.isoformat(timespec="seconds"),
                "experiment_record_id": record_id,
                "experiment_family": experiment_family,
                "base_condition_id": base_condition_id,
                "csv_output_path": csv_path,
                "input_volume": input_volume_name,
                "dataset": input_volume_name,
                "projection_mode": self.projectionMode,
                "projection_mode_status": self._widget_text("projectionModeStatusLabel"),
                "angle_group": "",
                "distance_group": "",
                "point_id": point_id,
                "markers_sorted": int(bool(self._markersSorted)),
                "perspective_calibration_ready": perspective_calibration_ready,
                "orthographic_calibration_ready": orthographic_calibration_ready,
                "shot1_available": int(self._get_shot_node_by_index(1) is not None),
                "shot2_available": int(self._get_shot_node_by_index(2) is not None),
                "shot3_available": int(self._get_shot_node_by_index(3) is not None),
                "shot2_angle_deg": shot2_angle_deg,
                "shot3_angle_m3_m1_deg": shot3_angle_m3_m1_deg,
                "shot3_angle_m3_m2_deg": shot3_angle_m3_m2_deg,
                "noise_type": normalized_noise_type,
                "noise_sigma_px": normalized_noise_sigma_px if normalized_noise_sigma_px is not None else "",
                "tre_mm_raw": tre_value_mm,
                "re_px_raw": reprojection_error_px,
                "ray_gap_mm_raw": ray_gap_mm,
                "tre_value_mm": tre_value_mm,
                "reprojection_error_px": reprojection_error_px,
                "ray_gap_mm": ray_gap_mm,
                "tre_display": tre_display,
                "reprojection_display": reproj_display,
                "ray_gap_display": line_gap_display,
                "is_clean_baseline": is_clean_baseline,
                "controlled_perturbation_enabled": int(
                    bool(self.controlledPerturbationEnabled or perturbation_state)
                ),
                "controlled_perturbation_noise_type": raw_noise_type,
                "controlled_perturbation_noise_sigma_px": (
                    raw_noise_sigma_px if self._normalize_optional_csv_value(raw_noise_sigma_px) is not None else ""
                ),
                "controlled_perturbation_run_id": perturbation_state.get("run_id", ""),
                "controlled_perturbation_run_stage": perturbation_state.get("stage", ""),
                "point1_selector": self._selector_current_node_name("point1Selector"),
                "point2_selector": self._selector_current_node_name("point2Selector"),
                "reprojection_point1_selector": self._selector_current_node_name("reprojectionPoint1Selector"),
                "reprojection_point2_selector": self._selector_current_node_name("reprojectionPoint2Selector"),
                "red_2d_selector": self._selector_current_node_name("Red2DPSelector"),
                "green_2d_selector": self._selector_current_node_name("Green2DPSelector"),
                "knife_selector": self._selector_current_node_name("knifeSelector"),
                "camera_view_angle_deg": float(camera_view_angle_deg) if camera_view_angle_deg is not None else "",
                "view_orthographic_enabled": int(self.projectionMode == "orthographic"),
                "red_uses_blackcenter_auto_point": red_uses_blackcenter_auto_point,
                "green_uses_blackcenter_auto_point": green_uses_blackcenter_auto_point,
                "uses_blackcenter_auto_point_any": int(
                    bool(red_uses_blackcenter_auto_point or green_uses_blackcenter_auto_point)
                ),
                "testpoint_x": testpoint_x,
                "testpoint_y": testpoint_y,
                "testpoint_z": testpoint_z,
                "testpoint_marker_distance_t1_mm": testpoint_marker_distance_t1_mm,
                "testpoint_marker_distance_t2_mm": testpoint_marker_distance_t2_mm,
                "testpoint_marker_distance_t3_mm": testpoint_marker_distance_t3_mm,
                "testpoint_marker_distance_mean_mm": testpoint_marker_distance_mean_mm,
                "repeat_index": "",
                "success_flag": "",
                "failure_reason": "",
                "marker_sort_rms": "",
                "calibration_reproj_rms": "",
                "debug_visualization": int(bool(self.debugVisualization)),
                "debug_plane_scale": float(self.debugPlaneScale),
                "debug_ray_scale": float(self.debugRayScale),
            }
            row.update(transform_snapshot_info)

            for view_idx in (1, 2, 3):
                row[f"marker_sort_view{view_idx}_rms_px"] = self._marker_sort_metric(view_idx, "rms_px")
                row[f"marker_sort_view{view_idx}_second_rms_px"] = self._marker_sort_metric(view_idx, "second_rms_px")
                row[f"marker_sort_view{view_idx}_rms_gap_px"] = self._marker_sort_metric(view_idx, "rms_gap_px")
                row[f"marker_sort_view{view_idx}_flip_x"] = self._marker_sort_metric(view_idx, "flip_x")
                row[f"marker_sort_view{view_idx}_flip_y"] = self._marker_sort_metric(view_idx, "flip_y")

            calibration_sets = {
                "perspective": self._get_active_perspective_calibrations(),
                "orthographic": self._get_active_orthographic_calibrations(),
            }
            for mode_name, calibs in calibration_sets.items():
                calibs = calibs if isinstance(calibs, dict) else {}
                for view_idx in (1, 2, 3):
                    calib = calibs.get(view_idx, {})
                    row[f"{mode_name}_calibration_view{view_idx}_reproj_rms_px"] = calib.get("reproj_rms_px", "")
                    row[f"{mode_name}_calibration_view{view_idx}_flip_x"] = (
                        int(bool(calib["flip_x"])) if "flip_x" in calib else ""
                    )
                    row[f"{mode_name}_calibration_view{view_idx}_flip_y"] = (
                        int(bool(calib["flip_y"])) if "flip_y" in calib else ""
                    )
                    row[f"{mode_name}_calibration_view{view_idx}_swap_big_23"] = (
                        int(bool(calib["swap_big_23"])) if "swap_big_23" in calib else ""
                    )
                    row[f"{mode_name}_calibration_view{view_idx}_swap_small_23"] = (
                        int(bool(calib["swap_small_23"])) if "swap_small_23" in calib else ""
                    )
                    row[f"{mode_name}_calibration_view{view_idx}_view_angle_deg"] = calib.get("view_angle_deg", "")

            row["marker_sort_rms"] = self._mean_numeric_values(
                [row[f"marker_sort_view{view_idx}_rms_px"] for view_idx in (1, 2, 3)]
            )
            active_mode_name = "orthographic" if self.projectionMode == "orthographic" else "perspective"
            row["calibration_reproj_rms"] = self._mean_numeric_values(
                [row[f"{active_mode_name}_calibration_view{view_idx}_reproj_rms_px"] for view_idx in (1, 2, 3)]
            )

            repeat_index = ""
            if experiment_family == "controlled_perturbation_study" and normalized_noise_type:
                repeat_index = self._count_existing_experiment_repeats(
                    csv_path,
                    base_condition_id,
                    normalized_noise_type,
                    normalized_noise_sigma_px,
                ) + 1
            row["repeat_index"] = repeat_index

            failure_reasons = []
            if not self._markersSorted:
                failure_reasons.append("markers_not_sorted")
            if self.projectionMode == "perspective" and not perspective_calibration_ready:
                failure_reasons.append("perspective_calibration_incomplete")
            if self.projectionMode == "orthographic" and not orthographic_calibration_ready:
                failure_reasons.append("orthographic_calibration_incomplete")
            if tre_value_mm in ("", None):
                failure_reasons.append("missing_tre_mm_raw")
            if reprojection_error_px in ("", None):
                failure_reasons.append("missing_re_px_raw")
            if ray_gap_mm in ("", None):
                failure_reasons.append("missing_ray_gap_mm_raw")
            if experiment_family == "controlled_perturbation_study":
                perturbation_stage = perturbation_state.get("stage", "")
                if perturbation_stage and perturbation_stage != "completed":
                    failure_reasons.append(f"perturbation_stage_{perturbation_stage}")
                elif not perturbation_stage and bool(self.controlledPerturbationEnabled):
                    failure_reasons.append("perturbation_not_run")
                if is_clean_baseline and isinstance(repeat_index, int) and repeat_index > 1:
                    failure_reasons.append("duplicate_baseline_for_base_condition")
            row["success_flag"] = int(len(failure_reasons) == 0)
            row["failure_reason"] = ";".join(failure_reasons)

            timing_keys = (
                "shot1_all_ms",
                "shot2_all_ms",
                "shot3_all_ms",
                "black_center_ms",
                "markers_sort_ms",
                "init_markers_ms",
                "perspective_calibration_ms",
                "orthographic_calibration_ms",
                "red_push_ms",
                "green_push_ms",
                "tre_calc_ms",
                "reprojection_calc_ms",
            )
            for timing_key in timing_keys:
                row[f"timing_{timing_key}"] = self.stepTimingsMs.get(timing_key, "")

            text_fields = {
                "timestamp",
                "experiment_record_id",
                "experiment_family",
                "base_condition_id",
                "csv_output_path",
                "input_volume",
                "dataset",
                "projection_mode",
                "projection_mode_status",
                "angle_group",
                "distance_group",
                "point_id",
                "noise_type",
                "tre_display",
                "reprojection_display",
                "ray_gap_display",
                "controlled_perturbation_noise_type",
                "controlled_perturbation_run_stage",
                "failure_reason",
                "point1_selector",
                "point2_selector",
                "reprojection_point1_selector",
                "reprojection_point2_selector",
                "red_2d_selector",
                "green_2d_selector",
                "knife_selector",
                "marker_transform_snapshot_dir",
                "marker_transform_1_name",
                "marker_transform_1_path",
                "marker_transform_2_name",
                "marker_transform_2_path",
                "marker_transform_3_name",
                "marker_transform_3_path",
            }
            for field_name, field_value in list(row.items()):
                if field_name in text_fields:
                    continue
                row[field_name] = self._csv_value_or_na(field_value)

            self._append_csv_row_with_schema_upgrade(csv_path, row)

            logging.info(f"Current results exported to CSV: {csv_path}")
            if show_feedback:
                try:
                    slicer.util.infoDisplay(f"Current results exported to CSV:\n{csv_path}")
                except Exception:
                    pass
            return csv_path
        except Exception as e:
            if show_feedback:
                self._error(f"Error exporting current results to CSV: {str(e)}", detailedText=f"{e}")
            raise

    def onSaveResultsCsv(self):
        """Append current test outputs and key runtime status fields into the export CSV."""
        try:
            self._save_current_results_csv(show_feedback=True)
        except Exception:
            return

    def onDebugVisToggle(self, enabled):
        """界面回调：执行 `onDebugVisToggle` 对应的交互处理流程。"""
        self.debugVisualization = enabled
        
        # Toggle visibility of all visualization nodes if they exist
        visNodeNames = [
            "vis_RedRay", "vis_GreenRay",
            "vis_RedBigIntersection", "vis_RedSmallIntersection",
            "vis_GreenBigIntersection", "vis_GreenSmallIntersection",
            "vis_GreenBigPlane", "vis_GreenSmallPlane",
            "vis_RedBigPlane", "vis_RedSmallPlane",
            "vis_YellowBigPlane", "vis_YellowSmallPlane",
            "vis_YellowRay", "vis_YellowBigIntersection", "vis_YellowSmallIntersection",
            "vis_TargetP3DMidpoint"
        ]
        
        for nodeName in visNodeNames:
            node = slicer.mrmlScene.GetFirstNodeByName(nodeName)
            if node:
                displayNode = node.GetDisplayNode()
                if displayNode:
                    displayNode.SetVisibility(enabled)

        if enabled:
            self._refreshDebugVisualization()

    def onDebugPlaneScaleChanged(self, value):
        """界面回调：执行 `onDebugPlaneScaleChanged` 对应的交互处理流程。"""
        self.debugPlaneScale = float(value)
        if self.debugVisualization:
            self._refreshDebugVisualization(refreshPlanes=True, refreshRays=False)

    def onDebugRayScaleChanged(self, value):
        """界面回调：执行 `onDebugRayScaleChanged` 对应的交互处理流程。"""
        self.debugRayScale = float(value)
        if self.debugVisualization:
            self._refreshDebugVisualization(refreshPlanes=False, refreshRays=True)

    def _createOrUpdateVisualizationNode(self, nodeName, position=None, color=(1, 1, 1), 
                                        nodeType="MarkupsFiducial", linePoints=None, glyphScale=2.0):
        """通用可视化辅助函数：按名称创建或更新点/线调试节点并配置显示属性。"""
        # Remove existing node if present
        existingNode = slicer.mrmlScene.GetFirstNodeByName(nodeName)
        if existingNode:
            slicer.mrmlScene.RemoveNode(existingNode)
        
        if nodeType == "MarkupsFiducial" and position is not None:
            # Create fiducial node
            node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", nodeName)
            node.AddControlPoint(vtk.vtkVector3d(position[0], position[1], position[2]))
            node.SetNthControlPointLabel(0, nodeName)
            
            # Set display properties
            displayNode = node.GetDisplayNode()
            if not displayNode:
                displayNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsDisplayNode")
                node.SetAndObserveDisplayNodeID(displayNode.GetID())
            
            displayNode.SetGlyphScale(glyphScale)
            displayNode.SetSelectedColor(*color)
            displayNode.SetColor(*color)
            displayNode.SetVisibility(self.debugVisualization)
            
            return node
        
        elif nodeType == "MarkupsLine" and linePoints and len(linePoints) >= 2:
            # Create line node
            node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsLineNode", nodeName)
            
            for point in linePoints:
                node.AddControlPoint(vtk.vtkVector3d(point[0], point[1], point[2]))
            
            # Set display properties
            displayNode = node.GetDisplayNode()
            if not displayNode:
                displayNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsDisplayNode")
                node.SetAndObserveDisplayNodeID(displayNode.GetID())
            
            displayNode.SetLineWidth(2.0)
            displayNode.SetSelectedColor(*color)
            displayNode.SetColor(*color)
            displayNode.SetVisibility(self.debugVisualization)
            
            return node
        
        return None

    def _createOrUpdatePlaneModel(self, nodeName, p1, p2, p3, color=(0.5, 0.5, 0.5), opacity=0.2, scale=6.0):
        """用三点构建平面模型，并按调试参数设置颜色、透明度与可见性。"""
        existingNode = slicer.mrmlScene.GetFirstNodeByName(nodeName)
        if existingNode:
            slicer.mrmlScene.RemoveNode(existingNode)

        p1 = np.array(p1)
        p2 = np.array(p2)
        p3 = np.array(p3)

        center = (p1 + p2 + p3) / 3.0
        u = p2 - p1
        v = p3 - p1
        u_scaled = u * scale
        v_scaled = v * scale

        origin = center - 0.5 * u_scaled - 0.5 * v_scaled
        point1 = origin + u_scaled
        point2 = origin + v_scaled

        planeSource = vtk.vtkPlaneSource()
        planeSource.SetOrigin(origin[0], origin[1], origin[2])
        planeSource.SetPoint1(point1[0], point1[1], point1[2])
        planeSource.SetPoint2(point2[0], point2[1], point2[2])
        planeSource.SetXResolution(1)
        planeSource.SetYResolution(1)
        planeSource.Update()

        modelNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode", nodeName)
        modelNode.SetAndObservePolyData(planeSource.GetOutput())

        displayNode = modelNode.GetDisplayNode()
        if not displayNode:
            displayNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelDisplayNode")
            modelNode.SetAndObserveDisplayNodeID(displayNode.GetID())

        displayNode.SetColor(*color)
        displayNode.SetOpacity(opacity)
        displayNode.SetVisibility(self.debugVisualization)

        return modelNode

    def _getExtendedLinePoints(self, p1, p2, scale=10.0):
        """获取 `_getExtendedLinePoints` 相关对象或计算结果。"""
        p1 = np.array(p1)
        p2 = np.array(p2)
        direction = p2 - p1
        length = np.linalg.norm(direction)
        if length == 0:
            return [p1, p2]
        direction /= length
        extra = length * (scale - 1.0) * 0.5
        return [p1 - direction * extra, p2 + direction * extra]

    def _refreshDebugVisualization(self, refreshPlanes=True, refreshRays=True):
        """在调试模式下刷新所有平面、射线和交点的可视化节点。"""
        if not self.debugVisualization:
            return

        if refreshPlanes:
            if hasattr(self, "bigMarker3DDic2") and hasattr(self, "smallMarker3DDic2"):
                greenBig_p1 = np.array(self.bigMarker3DDic2[1])
                greenBig_p2 = np.array(self.bigMarker3DDic2[2])
                greenBig_p3 = np.array(self.bigMarker3DDic2[3])
                greenSmall_p1 = np.array(self.smallMarker3DDic2[1])
                greenSmall_p2 = np.array(self.smallMarker3DDic2[2])
                greenSmall_p3 = np.array(self.smallMarker3DDic2[3])
                self._createOrUpdatePlaneModel(
                    "vis_GreenBigPlane",
                    greenBig_p1, greenBig_p2, greenBig_p3,
                    color=(0.0, 0.7, 0.7),
                    opacity=0.2,
                    scale=self.debugPlaneScale
                )
                self._createOrUpdatePlaneModel(
                    "vis_GreenSmallPlane",
                    greenSmall_p1, greenSmall_p2, greenSmall_p3,
                    color=(0.0, 0.4, 0.4),
                    opacity=0.2,
                    scale=self.debugPlaneScale
                )

            if hasattr(self, "bigMarker3DDic1") and hasattr(self, "smallMarker3DDic1"):
                redBig_p1 = np.array(self.bigMarker3DDic1[1])
                redBig_p2 = np.array(self.bigMarker3DDic1[2])
                redBig_p3 = np.array(self.bigMarker3DDic1[3])
                redSmall_p1 = np.array(self.smallMarker3DDic1[1])
                redSmall_p2 = np.array(self.smallMarker3DDic1[2])
                redSmall_p3 = np.array(self.smallMarker3DDic1[3])
                self._createOrUpdatePlaneModel(
                    "vis_RedBigPlane",
                    redBig_p1, redBig_p2, redBig_p3,
                    color=(0.8, 0.2, 0.2),
                    opacity=0.2,
                    scale=self.debugPlaneScale
                )
                self._createOrUpdatePlaneModel(
                    "vis_RedSmallPlane",
                    redSmall_p1, redSmall_p2, redSmall_p3,
                    color=(0.5, 0.1, 0.1),
                    opacity=0.2,
                    scale=self.debugPlaneScale
                )

            if hasattr(self, "bigMarker3DDic3") and hasattr(self, "smallMarker3DDic3"):
                yellowBig_p1 = np.array(self.bigMarker3DDic3[1])
                yellowBig_p2 = np.array(self.bigMarker3DDic3[2])
                yellowBig_p3 = np.array(self.bigMarker3DDic3[3])
                yellowSmall_p1 = np.array(self.smallMarker3DDic3[1])
                yellowSmall_p2 = np.array(self.smallMarker3DDic3[2])
                yellowSmall_p3 = np.array(self.smallMarker3DDic3[3])
                self._createOrUpdatePlaneModel(
                    "vis_YellowBigPlane",
                    yellowBig_p1, yellowBig_p2, yellowBig_p3,
                    color=(0.9, 0.8, 0.2),
                    opacity=0.2,
                    scale=self.debugPlaneScale
                )
                self._createOrUpdatePlaneModel(
                    "vis_YellowSmallPlane",
                    yellowSmall_p1, yellowSmall_p2, yellowSmall_p3,
                    color=(0.7, 0.6, 0.1),
                    opacity=0.2,
                    scale=self.debugPlaneScale
                )

        if refreshRays:
            if hasattr(self, "p3DBigRed") and hasattr(self, "p3DSmallRed"):
                redRayPoints = self._getExtendedLinePoints(self.p3DBigRed, self.p3DSmallRed, scale=self.debugRayScale)
                self._createOrUpdateVisualizationNode(
                    "vis_RedRay",
                    nodeType="MarkupsLine",
                    color=(1, 0, 0),
                    linePoints=redRayPoints
                )
            if hasattr(self, "p3DBigGreen") and hasattr(self, "p3DSmallGreen"):
                greenRayPoints = self._getExtendedLinePoints(self.p3DBigGreen, self.p3DSmallGreen, scale=self.debugRayScale)
                self._createOrUpdateVisualizationNode(
                    "vis_GreenRay",
                    nodeType="MarkupsLine",
                    color=(0, 1, 0),
                    linePoints=greenRayPoints
                )

            if hasattr(self, "yellow3DVec") and hasattr(self, "p3DTarget"):
                yellowRayPoints = self._getExtendedLinePoints(
                    self.p3DTarget - self.yellow3DVec,
                    self.p3DTarget + self.yellow3DVec,
                    scale=self.debugRayScale
                )
                self._createOrUpdateVisualizationNode(
                    "vis_YellowRay",
                    nodeType="MarkupsLine",
                    color=(1, 1, 0),
                    linePoints=yellowRayPoints
                )

                if hasattr(self, "bigMarker3DDic3") and hasattr(self, "smallMarker3DDic3"):
                    yellowBig_p1 = np.array(self.bigMarker3DDic3[1])
                    yellowBig_p2 = np.array(self.bigMarker3DDic3[2])
                    yellowBig_p3 = np.array(self.bigMarker3DDic3[3])
                    yellowSmall_p1 = np.array(self.smallMarker3DDic3[1])
                    yellowSmall_p2 = np.array(self.smallMarker3DDic3[2])
                    yellowSmall_p3 = np.array(self.smallMarker3DDic3[3])

                    yellowBigIntersectionP3D = self.logic.line2plane_intersection(
                        self.p3DTarget - self.yellow3DVec,
                        self.p3DTarget + self.yellow3DVec,
                        yellowBig_p1, yellowBig_p2, yellowBig_p3
                    )
                    yellowSmallIntersectionP3D = self.logic.line2plane_intersection(
                        self.p3DTarget - self.yellow3DVec,
                        self.p3DTarget + self.yellow3DVec,
                        yellowSmall_p1, yellowSmall_p2, yellowSmall_p3
                    )

                    if yellowBigIntersectionP3D is not None:
                        self._createOrUpdateVisualizationNode(
                            "vis_YellowBigIntersection",
                            position=yellowBigIntersectionP3D,
                            color=(1, 1, 0),
                            nodeType="MarkupsFiducial",
                            glyphScale=1.0
                        )
                    if yellowSmallIntersectionP3D is not None:
                        self._createOrUpdateVisualizationNode(
                            "vis_YellowSmallIntersection",
                            position=yellowSmallIntersectionP3D,
                            color=(1, 1, 0),
                            nodeType="MarkupsFiducial",
                            glyphScale=1.0
                        )





