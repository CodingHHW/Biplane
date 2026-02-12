import logging
import os
from typing import Annotated, Optional

import numpy as np
import vtk
import SimpleITK as sitk
import slicer
# Ensure OpenCV is available; install on demand for Slicer environment
try:
    import cv2
except ImportError:  # pragma: no cover - runtime install path
    slicer.util.pip_install("opencv-python")
    import cv2
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
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
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
    """Uses ScriptedLoadableModuleWidget base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent=None) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)  # needed for parameter node observation
        self.logic = None
        self._parameterNode = None
        self._parameterNodeGuiTag = None
        self._knifeObserverTag = None
        self.debugVisualization = False
        self.debugPlaneScale = 6.0
        self.debugRayScale = 10.0
        self.projectionMode = "orthographic"
        self.perspectiveViewCalibs = {}
        self._markersSorted = False
        basePath = getattr(getattr(slicer, "app", None), "temporaryPath", os.path.expanduser("~/Desktop"))
        self.savePath = os.path.join(basePath, "Biplane")
        if not os.path.exists(self.savePath):
            os.makedirs(self.savePath)

    def _error(self, message: str, detailedText: Optional[str] = None) -> None:
        try:
            slicer.util.errorDisplay(message, detailedText=detailedText)
        except Exception:
            logging.error(message)
            if detailedText:
                logging.error(detailedText)

    def _require_markers_sorted(self) -> bool:
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
                self._error("透视模式标定未完成，请重新点击 markersSort")
                return False
        return True

    def _getBodyVolumeNode(self):
        if self._parameterNode and getattr(self._parameterNode, "inputVolume", None):
            return self._parameterNode.inputVolume
        return slicer.mrmlScene.GetFirstNodeByClass("vtkMRMLScalarVolumeNode")

    def _getMarkersModelNode(self):
        return slicer.mrmlScene.GetFirstNodeByName("markers")

    def _getThreeDView(self):
        lm = slicer.app.layoutManager()
        if not lm:
            return None
        threeDWidget = lm.threeDWidget(0)
        if not threeDWidget:
            return None
        return threeDWidget.threeDView()

    def _captureViewToFile(self, filepath: str) -> bool:
        view = self._getThreeDView()
        if not view:
            self._error("3D 视图不可用，无法截图")
            return False
        view.forceRender()
        cap = ScreenCapture.ScreenCaptureLogic()
        cap.captureImageFromView(view, filepath)
        return os.path.exists(filepath)

    def _open_transforms_module(self) -> None:
        try:
            slicer.util.selectModule("Transforms")
        except Exception:
            self._error("无法切换到 Transforms 模块")

    def _limit_display_nodes_for_shot(self, allowed_displayable_nodes):
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
        for display_node, visibility, visibility3d in visibility_backup:
            if display_node:
                display_node.SetVisibility(visibility)
                if visibility3d is not None and hasattr(display_node, "SetVisibility3D"):
                    display_node.SetVisibility3D(visibility3d)

    def _requireImage(self, filepath: str, label: str):
        if not os.path.exists(filepath):
            self._error(f"缺少 {label} 文件：{filepath}")
            return None
        img = cv2.imread(filepath, cv2.IMREAD_UNCHANGED)
        if img is None:
            self._error(f"无法读取 {label} 文件：{filepath}")
        return img

    def _get_black_center_from_volume(self, volumeNode):
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
        if selector is None or node is None:
            return
        if hasattr(selector, "setCurrentNode"):
            selector.setCurrentNode(node)
        elif hasattr(selector, "setCurrentNodeID"):
            selector.setCurrentNodeID(node.GetID())

    def _plane_normal_from_marker_dict(self, marker_dict):
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
        n1 = self._plane_normal_from_marker_dict(marker_dict_a)
        n2 = self._plane_normal_from_marker_dict(marker_dict_b)
        if n1 is None or n2 is None:
            return None
        dot = float(np.clip(np.dot(n1, n2), -1.0, 1.0))
        return float(np.degrees(np.arccos(abs(dot))))

    def _get_big_marker_plane_dict(self, index: int):
        attr_name = f"bigMarker3DDic{index}"
        existing = getattr(self, attr_name, None)
        if existing:
            return existing
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
        setattr(self, attr_name, marker_dict)
        return marker_dict

    def _set_angle_display(self, label_widget, line_edit, angle_value):
        if label_widget is not None:
            label_widget.setVisible(True)
        if line_edit is not None:
            line_edit.setVisible(True)
            if angle_value is None:
                line_edit.setText("n/a")
            else:
                line_edit.setText(f"{angle_value:.2f}")

    def _update_shot2_angle_display(self):
        angle = self._compute_plane_angle_deg(
            self._get_big_marker_plane_dict(1),
            self._get_big_marker_plane_dict(2),
        )
        self._set_angle_display(self.ui.labelShot2Angle, self.ui.shot2AngleLineEdit, angle)

    def _update_shot3_angle_display(self):
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
        """Called when the user opens the module the first time and the widget is initialized."""
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

        self.ui.orthographicProjectionButton.setChecked(True)
        self.ui.perspectiveProjectionButton.setChecked(False)
        self._updateProjectionModeStatusLabel()
        self._applyProjectionModeToView()

        # Make sure parameter node is initialized (needed for module reload)
        self.initializeParameterNode()

    def cleanup(self) -> None:
        """Called when the application closes and the module widget is destroyed."""
        self.removeObservers()

    def enter(self) -> None:
        """Called each time the user opens this module."""
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
        if not hasattr(self.ui, "projectionModeStatusLabel"):
            return
        mode_text = "Orthographic" if self.projectionMode == "orthographic" else "Perspective"
        self.ui.projectionModeStatusLabel.setText(f"Current mode: {mode_text}")

    def _setProjectionMode(self, mode: str) -> None:
        if mode not in ("orthographic", "perspective"):
            return
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
        self._setProjectionMode("orthographic")

    def onPerspectiveProjectionButton(self):
        self._setProjectionMode("perspective")

    def _get_shot_node_by_index(self, view_index: int):
        return slicer.mrmlScene.GetFirstNodeByName(f"shot{view_index}")

    def _get_shot_image_size(self, view_index: int):
        shot_node = self._get_shot_node_by_index(view_index)
        if not shot_node or not shot_node.GetImageData():
            return None
        dims = shot_node.GetImageData().GetDimensions()
        if dims[0] <= 0 or dims[1] <= 0:
            return None
        return int(dims[0]), int(dims[1])

    def _get_current_3d_camera_view_angle(self) -> float:
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

    def _get_view_marker_pairs(self, view_index: int):
        big_2d = getattr(self, f"bigMarkersSort{view_index}")
        small_2d = getattr(self, f"smallMarkersSort{view_index}")
        big_3d = getattr(self, f"bigMarker3DDic{view_index}")
        small_3d = getattr(self, f"smallMarker3DDic{view_index}")

        image_points = []
        object_points = []
        for label in (1, 2, 3, 4, 5):
            image_points.append(get_key_by_value(big_2d, label)[0:2])
            object_points.append(big_3d[label])
        for label in (1, 2, 3, 4, 5):
            image_points.append(get_key_by_value(small_2d, label)[0:2])
            object_points.append(small_3d[label])

        return np.array(object_points, dtype=np.float64), np.array(image_points, dtype=np.float64)

    def _slicer2d_to_pixel_raw(self, point2d_slicer: np.array, image_width: int, image_height: int, flip_x: bool, flip_y: bool):
        u = -float(point2d_slicer[0])
        v = -float(point2d_slicer[1])
        if flip_x:
            u = (float(image_width) - 1.0) - u
        if flip_y:
            v = (float(image_height) - 1.0) - v
        return np.array([u, v], dtype=np.float64)

    def _pixel_to_slicer2d_raw(self, pixel2d: np.array, image_width: int, image_height: int, flip_x: bool, flip_y: bool):
        u = float(pixel2d[0])
        v = float(pixel2d[1])
        if flip_x:
            u = (float(image_width) - 1.0) - u
        if flip_y:
            v = (float(image_height) - 1.0) - v
        x = -u
        y = -v
        return np.array([x, y], dtype=np.float64)

    def _compute_perspective_calibrations(self) -> bool:
        try:
            view_angle = self._get_current_3d_camera_view_angle()
            calibs = {}
            for idx in (1, 2, 3):
                image_size = self._get_shot_image_size(idx)
                if not image_size:
                    raise ValueError(f"shot{idx} 图像尺寸不可用")
                image_width, image_height = image_size
                camera_matrix, dist_coeffs = self.logic.buildCameraIntrinsics(image_width, image_height, view_angle)
                object_points, image_points = self._get_view_marker_pairs(idx)

                best = None
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
                            best = (rms, flip_x, flip_y, rvec, tvec)

                if best is None:
                    raise ValueError(f"view{idx} solvePnP 标定失败")
                rms, flip_x, flip_y, rvec, tvec = best
                logging.info(f"PnP calib view{idx}: reproj RMS={rms:.3f}px, flip_x={flip_x}, flip_y={flip_y}, fov={view_angle:.2f}deg")

                calibs[idx] = {
                    "K": camera_matrix,
                    "dist": dist_coeffs,
                    "rvec": rvec,
                    "tvec": tvec,
                    "w": int(image_width),
                    "h": int(image_height),
                    "flip_x": bool(flip_x),
                    "flip_y": bool(flip_y),
                }

            self.perspectiveViewCalibs = calibs
            return True
        except Exception as e:
            self.perspectiveViewCalibs = {}
            self._error("透视模式 solvePnP 标定失败", detailedText=str(e))
            return False

    def _pixel_to_world_ray(self, view_index: int, pixel_2d: np.array):
        calib = self.perspectiveViewCalibs.get(view_index)
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
        calib = self.perspectiveViewCalibs.get(view_index)
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
        transformNames = ["LinearTransform", "LinearTransform_1", "LinearTransform_2"]
        for name in transformNames:
            node = slicer.mrmlScene.GetFirstNodeByName(name)
            if node is None:
                node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLinearTransformNode", name)

    def exit(self) -> None:
        """Called each time the user opens a different module."""
        # Do not react to parameter node changes (GUI will be updated when the user enters into the module)
        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self._parameterNodeGuiTag = None
            self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)

    def onSceneStartClose(self, caller, event) -> None:
        """Called just before the scene is closed."""
        # Parameter node will be reset, do not use it anymore
        self.setParameterNode(None)
        self._markersSorted = False

    def onSceneEndClose(self, caller, event) -> None:
        """Called just after the scene is closed."""
        # If this module is shown while the scene is closed then recreate a new parameter node immediately
        if self.parent.isEntered:
            self.initializeParameterNode()

    def initializeParameterNode(self) -> None:
        """Ensure parameter node exists and observed."""
        # Parameter node stores all user choices in parameter values, node selections, etc.
        # so that when the scene is saved and reloaded, these settings are restored.

        self.setParameterNode(self.logic.getParameterNode())

        # Select default input nodes if nothing is selected yet to save a few clicks for the user
        if not self._parameterNode.inputVolume:
            firstVolumeNode = slicer.mrmlScene.GetFirstNodeByClass("vtkMRMLScalarVolumeNode")
            if firstVolumeNode:
                self._parameterNode.inputVolume = firstVolumeNode

    def setParameterNode(self, inputParameterNode: Optional[BiplaneParameterNode]) -> None:
        """
        Set and observe parameter node.
        Observation is needed because when the parameter node is changed then the GUI must be updated immediately.
        """

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
        canRun = bool(self._parameterNode and getattr(self._parameterNode, "inputVolume", None))
        self.ui.shot1AllButton.enabled = canRun
        self.ui.shot2AllButton.enabled = canRun
        self.ui.shot3AllButton.enabled = canRun
        self.ui.shot1AllButton.toolTip = _("Select input volume" if not canRun else "Capture shot")

    def flipImage(self, image):
        tmp = sitk.GetImageFromArray(sitk.GetArrayFromImage(image))
        flipFilter = sitk.FlipImageFilter()
        flipFilter.SetFlipAxes([True, True, False])
        imageMirror = flipFilter.Execute(tmp)
        imageMirror.CopyInformation(image)
        return imageMirror

    def sitk_image_to_vtk_image(self, sitk_image):
        # 将 SimpleITK 图像转换为 NumPy 数组
        # sitk_image = self.flipImage(sitk_image)
        np_array = sitk.GetArrayViewFromImage(sitk_image)

        # 获取图像的尺寸
        size = sitk_image.GetSize()

        # 创建一个 vtkImageData 对象
        vtk_image = vtk.vtkImageData()
        vtk_image.SetDimensions(size[0], size[1], 1)
        vtk_image.SetSpacing(sitk_image.GetSpacing()[0], sitk_image.GetSpacing()[1], 1)
        vtk_image.SetOrigin(sitk_image.GetOrigin()[0], sitk_image.GetOrigin()[1], 0)

        # 将 NumPy 数组数据分配给 vtkImageData
        vtk_array = vtk.util.numpy_support.numpy_to_vtk(np_array.ravel(), deep=True, array_type=vtk.VTK_FLOAT)
        vtk_image.GetPointData().SetScalars(vtk_array)

        return vtk_image

    def vtk_image_to_sitk_image(self, vtk_image):
        # 获取vtkImageData的原始数据
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
        img = vtk.vtkImageData()
        img.SetDimensions(array.shape[1], array.shape[0], 1)
        img.SetSpacing(1,1,1)
        img.SetOrigin(0,0,0)
        vtk_data = numpy_to_vtk(array.ravel(), array_type=vtk.VTK_FLOAT)
        img.GetPointData().SetScalars(vtk_data)
        return img

    def onShowVolumeButton(self):
        bodyVolumeNode = self._getBodyVolumeNode()
        if not bodyVolumeNode:
            self._error("未找到输入 Volume，请先在 Input volume 中选择")
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
        markerModelNode = self._getMarkersModelNode()
        if not markerModelNode:
            self._error("需要先点击 showMarker 生成 markers")
            return

        polyData = markerModelNode.GetPolyData()
        if polyData is None or polyData.GetNumberOfPoints() == 0:
            self._error("markers 没有有效点数据")
            return

        center = [0.0, 0.0, 0.0]
        polyData.GetCenter(center)

        testPointNode = slicer.mrmlScene.GetFirstNodeByName("testPoint")
        if testPointNode is None:
            testPointNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", "testPoint")
            testPointNode.CreateDefaultDisplayNodes()

        if testPointNode.GetNumberOfControlPoints() < 1:
            testPointNode.AddControlPoint(center)
        else:
            testPointNode.SetNthControlPointPosition(0, center)

        displayNode = testPointNode.GetDisplayNode()
        if displayNode:
            displayNode.SetPointLabelsVisibility(False)
            displayNode.SetSelectedColor(0, 0, 0)
            displayNode.SetColor(0, 0, 0)
            displayNode.SetVisibility(True)
            if hasattr(displayNode, "SetVisibility3D"):
                displayNode.SetVisibility3D(True)
            if hasattr(displayNode, "SetVisibility2D"):
                displayNode.SetVisibility2D(False)

    def onShowKnifeButton(self):
        volumeNode = self._getBodyVolumeNode()
        if not volumeNode:
            self._error("未找到输入 Volume，请先在 Input volume 中选择")
            return
        center = self._get_volume_center(volumeNode)
        if center is None:
            self._error("无法获取 Volume 中心点")
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
        source_node = slicer.mrmlScene.GetFirstNodeByName(source_name)
        if source_node is None or source_node.GetNumberOfControlPoints() < 1:
            self._error(f"未找到 {source_name} 的控制点")
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
        self._copy_center_to_point("blackCenter1", "PointRed", "vtkMRMLSliceNodeRed", (1.0, 0.0, 0.0))

    def onCopyBlackCenter2(self):
        self._copy_center_to_point("blackCenter2", "PointGreen", "vtkMRMLSliceNodeGreen", (0.0, 1.0, 0.0))

    def onShot1Button(self):
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("需要先加载 Volume 并点击 showMarker 生成 markers")
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
        self.onShot1Button()
        self.onShot1ButtonAgain()
        self.onShot1ButtonShow()
        self.onShowVolumeButton()
        self._ensure_center_fiducial("PointRed", "vtkMRMLSliceNodeRed", (1.0, 0.0, 0.0))
        point_red = slicer.mrmlScene.GetFirstNodeByName("PointRed")
        self._set_selector_current_node(self.ui.Red2DPSelector, point_red)


    def onShot1ButtonAgain(self):
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("需要先加载 Volume 并点击 showMarker 生成 markers")
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
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("需要先加载 Volume 并点击 showMarker 生成 markers")
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
        self.onShot2Button()
        self.onShot2ButtonAgain()
        self.onShot2ButtonShow()
        self.onShowVolumeButton()
        self._ensure_center_fiducial("PointGreen", "vtkMRMLSliceNodeGreen", (0.0, 1.0, 0.0))
        point_green = slicer.mrmlScene.GetFirstNodeByName("PointGreen")
        self._set_selector_current_node(self.ui.Green2DPSelector, point_green)
        self._update_shot2_angle_display()


    def onShot2ButtonAgain(self):
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("需要先加载 Volume 并点击 showMarker 生成 markers")
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
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("需要先加载 Volume 并点击 showMarker 生成 markers")
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
        self.onShot3Button()
        self.onShot3ButtonAgain()
        self.onShot3ButtonShow()
        self.onShowVolumeButton()
        self._update_shot3_angle_display()

    def onShot3ButtonAgain(self):
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("需要先加载 Volume 并点击 showMarker 生成 markers")
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
            self._error("未找到 testPoint 像素中心，请确认图像中存在像素值为 -100 的点")
            return

        self._show_black_center_marker("blackCenter1", "vtkMRMLSliceNodeRed", pos1)
        self._show_black_center_marker("blackCenter2", "vtkMRMLSliceNodeGreen", pos2)
        self._show_black_center_marker("blackCenter3", "vtkMRMLSliceNodeYellow", pos3)

    def onMarkersSortButton(self):
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

        markerSortLogic1.getMarkerCenters(shot1ImageITK)
        self.bigMarkersSort1 = markerSortLogic1.bigMarkersSort()
        self.smallMarkersSort1 = markerSortLogic1.smallMarkersSort()

        markerSortLogic2.getMarkerCenters(shot2ImageITK)
        self.bigMarkersSort2 = markerSortLogic2.bigMarkersSort()
        self.smallMarkersSort2 = markerSortLogic2.smallMarkersSort()

        markerSortLogic3.getMarkerCenters(shot3ImageITK)
        self.bigMarkersSort3 = markerSortLogic3.bigMarkersSort()
        self.smallMarkersSort3 = markerSortLogic3.smallMarkersSort()

        # 转换成 slicer 坐标系
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

        # 调式显示点，判断2D图像上的点是否正确
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
        
        # 1, 先计算 3D 都 2D 的变换

        # 使用出厂设置的 marker 坐标
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
        # 这是第一个视图
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

        # 这是第二个视图
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


        # 这是第三个视图
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

        # *******************仿射变换（替代原透视变换）*******************
        # 使用出厂设置的 marker 坐标，全部 5 个点
        # 正交投影下平面→图像映射为仿射变换（6 DOF），
        # 使用 5 个点进行最小二乘拟合，比 4 点透视变换更鲁棒。
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
        # 这是第一个视图
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

        # 这是第二个视图
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

        # 这是第三个视图
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
            self._error(f"计算投影矩阵失败（模式: {projection_mode}）", detailedText=str(e))
            return False

        if projection_mode == "perspective":
            if not self._compute_perspective_calibrations():
                return False
        else:
            self.perspectiveViewCalibs = {}

        return True


    def _apply_transform_to_markers(self, transform_name: str):
        marker_model_node = self._getMarkersModelNode()
        if marker_model_node is None:
            self._error("未找到 markers 模型，请先点击 showMarker")
            return
        transform_node = slicer.mrmlScene.GetFirstNodeByName(transform_name)
        if transform_node is None:
            self._error(f"未找到 {transform_name} 节点")
            return
        marker_model_node.SetAndObserveTransformNodeID(transform_node.GetID())
        marker_model_node.SetDisplayVisibility(True)
        self._select_transform_in_transforms_module(transform_node)

    def _select_transform_in_transforms_module(self, transform_node):
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
        self._apply_transform_to_markers("LinearTransform")

    def onMarkers2Button(self):
        self._apply_transform_to_markers("LinearTransform_1")

    def onMarkers3Button(self):
        self._apply_transform_to_markers("LinearTransform_2")

    def onOpenTransforms(self):
        self._open_transforms_module()

    def initLightVec(self):
        if self.projectionMode == "perspective":
            markerNode1 = slicer.mrmlScene.GetFirstNodeByName("markers1")
            p1 = np.array(markerNode1.GetNthControlPointPosition(0)[0:2])
            o1, d1 = self._pixel_to_world_ray(1, p1)
            self.p3DBigRed = self.logic.rayPlaneIntersection(o1, d1, self.bigMarker3DDic1[1], self.bigMarker3DDic1[2], self.bigMarker3DDic1[3])
            self.p3DSmallRed = self.logic.rayPlaneIntersection(o1, d1, self.smallMarker3DDic1[1], self.smallMarker3DDic1[2], self.smallMarker3DDic1[3])
            if self.p3DBigRed is None or self.p3DSmallRed is None:
                self._error("透视模式下 Red 视图射线与标记平面求交失败")
                return
            self.red3DVec = np.array(self.p3DBigRed) - np.array(self.p3DSmallRed)

            markerNode2 = slicer.mrmlScene.GetFirstNodeByName("markers2")
            p2 = np.array(markerNode2.GetNthControlPointPosition(0)[0:2])
            o2, d2 = self._pixel_to_world_ray(2, p2)
            self.p3DBigGreen = self.logic.rayPlaneIntersection(o2, d2, self.bigMarker3DDic2[1], self.bigMarker3DDic2[2], self.bigMarker3DDic2[3])
            self.p3DSmallGreen = self.logic.rayPlaneIntersection(o2, d2, self.smallMarker3DDic2[1], self.smallMarker3DDic2[2], self.smallMarker3DDic2[3])
            if self.p3DBigGreen is None or self.p3DSmallGreen is None:
                self._error("透视模式下 Green 视图射线与标记平面求交失败")
                return
            self.green3DVec = np.array(self.p3DBigGreen) - np.array(self.p3DSmallGreen)

            markerNode3 = slicer.mrmlScene.GetFirstNodeByName("markers3")
            p3 = np.array(markerNode3.GetNthControlPointPosition(0)[0:2])
            o3, d3 = self._pixel_to_world_ray(3, p3)
            self.p3DBigYellow = self.logic.rayPlaneIntersection(o3, d3, self.bigMarker3DDic3[1], self.bigMarker3DDic3[2], self.bigMarker3DDic3[3])
            self.p3DSmallYellow = self.logic.rayPlaneIntersection(o3, d3, self.smallMarker3DDic3[1], self.smallMarker3DDic3[2], self.smallMarker3DDic3[3])
            if self.p3DBigYellow is None or self.p3DSmallYellow is None:
                self._error("透视模式下 Yellow 视图射线与标记平面求交失败")
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
        if not self._require_markers_sorted():
            return
        markupNode = self.ui.Red2DPSelector.currentNode()
        if not markupNode or markupNode.GetNumberOfControlPoints() < 1:
            self._error("请先在 Red 视图添加一个 2D 点")
            return
        # 在 Red 视图上的点只显示在 Red 视图
        displayNode = markupNode.GetDisplayNode()
        displayNode.SetVisibility(True)
        displayNode.SetViewNodeIDs(["vtkMRMLSliceNodeRed"])
        displayNode.SetVisibility3D(False)
        # displayNode.SetGlyphScale(0.6)
        displayNode.SetSelectedColor([0.941, 0.902, 0.549])

        p2D = np.array(markupNode.GetNthControlPointPosition(0)[0:2])
        if self.projectionMode == "perspective":
            ray_origin, ray_dir = self._pixel_to_world_ray(1, p2D)
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

        # 计算光线在Green 视图marker 平面的 big 与 small 的交点
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
            self._error("光线与 Green 视图平面求交失败")
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
        markupNode = self.ui.Green2DPSelector.currentNode()
        if not markupNode or markupNode.GetNumberOfControlPoints() < 1:
            self._error("请先在 Green 视图添加一个 2D 点")
            return
        # 在 Green 视图上的点只显示 Green 视图
        displayNode = markupNode.GetDisplayNode()
        displayNode.SetVisibility(True)
        displayNode.SetViewNodeIDs(["vtkMRMLSliceNodeGreen"])
        displayNode.SetVisibility3D(False)
        # displayNode.SetGlyphScale(0.6)
        displayNode.SetSelectedColor([0.392, 0.584, 0.929])

        p3D = np.array(markupNode.GetNthControlPointPosition(0))   #手动添加的点
        lineNodeGreen = slicer.mrmlScene.GetFirstNodeByName("GreenLine2D")
        lineP1 = np.array([0.0, 0.0, 0.0])
        lineP2 = np.array([0.0, 0.0, 0.0])
        if lineNodeGreen == None:
            self._error("需要先在 Red 视图点击 redPush 生成 GreenLine2D")
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
            
        # 计算手动添加的点到直线的最近点，将点自动移动到直线上
        p3DNearest2Line = self.logic.pointNearest2Line(p3D, lineP1, lineP2)
        # 替换手动添加的 markupNode
        markupNode.SetNthControlPointPosition(0, p3DNearest2Line)
        p2DNearest2Line = p3DNearest2Line[0:2]
        if self.projectionMode == "perspective":
            ray_origin, ray_dir = self._pixel_to_world_ray(2, p2DNearest2Line)
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
                self._error("Green 视图透视射线与标记平面求交失败")
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
        
        # 计算两条3D光线的空间交点
        line1_p1, line1_p2 = np.array(self.p3DBigRed), np.array(self.p3DSmallRed)
        line2_p1, line2_p2 = np.array(self.p3DBigGreen), np.array(self.p3DSmallGreen)

        p3D, line_gap = self.logic.line2line_closest_midpoint3D(line1_p1, line1_p2, line2_p1, line2_p2)
        if p3D is None:
            self._error("两条3D光线接近平行，无法稳定计算 TargetP3D")
            return
        self.p3DTarget = p3D
        if line_gap is not None:
            self.ui.lineGapDisplay.setText(f"{line_gap:.2f} mm")
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

        # 显示在 3D 空间的实际位置的点，该点是最终结果点
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

        # 计算同时显示第三个视图上的2D点
        if self.projectionMode == "perspective":
            intersectionP3DYellow = self._project_world_point_to_view(3, p3D)
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

        re_point1 = slicer.mrmlScene.GetFirstNodeByName("blackCenter2")
        re_point2 = slicer.mrmlScene.GetFirstNodeByName("PointGreen")
        self._set_selector_current_node(self.ui.reprojectionPoint1Selector, re_point1)
        self._set_selector_current_node(self.ui.reprojectionPoint2Selector, re_point2)


    def onTracing(self):
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
        knifeNode = self.ui.knifeSelector.currentNode()    
        p3D = np.array(knifeNode.GetNthControlPointPosition(0))
        if self.projectionMode == "perspective":
            p2DRed = self._project_world_point_to_view(1, p3D)
            p2DGreen = self._project_world_point_to_view(2, p3D)
            p2DYellow = self._project_world_point_to_view(3, p3D)
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
        
        # 显示追踪点
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
        """Calculate Target Registration Error (TRE) between two selected fiducial points."""
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
            
            # Display the result in the UI (formatted to 2 decimal places)
            self.ui.treValueDisplay.setText(f"{tre:.2f} mm")
            
            # Also log the result
            logging.info(f"TRE calculated: {tre:.4f} mm")
            logging.info(f"  Point 1: ({pos1[0]:.2f}, {pos1[1]:.2f}, {pos1[2]:.2f})")
            logging.info(f"  Point 2: ({pos2[0]:.2f}, {pos2[1]:.2f}, {pos2[2]:.2f})")
            
        except Exception as e:
            self._error(f"Error calculating TRE: {str(e)}", detailedText=f"{e}")

    def onCalculateReprojectionError(self):
        """Calculate reprojection error between two selected fiducial points."""
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
            self.ui.reprojectionValueDisplay.setText(f"{reproj_err:.2f} px")

            logging.info(f"Reprojection error calculated: {reproj_err:.4f} px")
            logging.info(f"  Point 1: ({pos1[0]:.2f}, {pos1[1]:.2f}, {pos1[2]:.2f})")
            logging.info(f"  Point 2: ({pos2[0]:.2f}, {pos2[1]:.2f}, {pos2[2]:.2f})")

        except Exception as e:
            self._error(f"Error calculating reprojection error: {str(e)}", detailedText=f"{e}")

    def onDebugVisToggle(self, enabled):
        """Toggle visibility of all debug visualization nodes"""
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
        self.debugPlaneScale = float(value)
        if self.debugVisualization:
            self._refreshDebugVisualization(refreshPlanes=True, refreshRays=False)

    def onDebugRayScaleChanged(self, value):
        self.debugRayScale = float(value)
        if self.debugVisualization:
            self._refreshDebugVisualization(refreshPlanes=False, refreshRays=True)

    def _createOrUpdateVisualizationNode(self, nodeName, position=None, color=(1, 1, 1), 
                                        nodeType="MarkupsFiducial", linePoints=None, glyphScale=2.0):
        """
        Helper method to create or update a visualization node.
        
        Args:
            nodeName: Unique name for the visualization node
            position: 3D point (for fiducial) or None (for line)
            color: RGB tuple (0-1 range)
            nodeType: Type of node ("MarkupsFiducial" or "MarkupsLine")
            linePoints: List of two 3D points if creating a line
        
        Returns:
            Created or existing node
        """
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
        """Create or update a plane model from three points."""
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
        """Return extended line endpoints so rays are easier to see."""
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



