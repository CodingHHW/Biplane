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

    def _requireImage(self, filepath: str, label: str):
        if not os.path.exists(filepath):
            self._error(f"缺少 {label} 文件：{filepath}")
            return None
        img = cv2.imread(filepath, cv2.IMREAD_UNCHANGED)
        if img is None:
            self._error(f"无法读取 {label} 文件：{filepath}")
        return img

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

        self.ui.shot1Button.connect("clicked(bool)", self.onShot1Button)
        self.ui.shot1ButtonAgain.connect("clicked(bool)", self.onShot1ButtonAgain)
        self.ui.shot1ButtonShow.connect("clicked(bool)", self.onShot1ButtonShow)

        self.ui.shot2Button.connect("clicked(bool)", self.onShot2Button)
        self.ui.shot2ButtonAgain.connect("clicked(bool)", self.onShot2ButtonAgain)
        self.ui.shot2ButtonShow.connect("clicked(bool)", self.onShot2ButtonShow)

        self.ui.shot3Button.connect("clicked(bool)", self.onShot3Button)
        self.ui.shot3ButtonAgain.connect("clicked(bool)", self.onShot3ButtonAgain)
        self.ui.shot3ButtonShow.connect("clicked(bool)", self.onShot3ButtonShow)

        self.ui.markersSortButton.connect("clicked(bool)", self.onMarkersSortButton)
        
        self.ui.redPushButton.connect("clicked(bool)", self.onTwoD2ThreeDRed)
        self.ui.greenPushButton.connect("clicked(bool)", self.onTwoD2ThreeDGreen)

        self.ui.tracingPushButton.connect("clicked(bool)", self.onTracing)

        self.ui.calculateTREButton.connect("clicked(bool)", self.onCalculateTRE)

        self.ui.debugVisCheckBox.connect("toggled(bool)", self.onDebugVisToggle)

        # Make sure parameter node is initialized (needed for module reload)
        self.initializeParameterNode()

    def cleanup(self) -> None:
        """Called when the application closes and the module widget is destroyed."""
        self.removeObservers()

    def enter(self) -> None:
        """Called each time the user opens this module."""
        # Make sure parameter node exists and observed
        self.initializeParameterNode()

        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        threeDControllerWidget = threeDWidget.threeDController()
        threeDControllerWidget.setOrthographicModeEnabled(True)

        viewNode = slicer.mrmlScene.GetNodeByID("vtkMRMLViewNode1")
        viewNode.SetBackgroundColor(1, 1, 1)
        viewNode.SetBackgroundColor2(1, 1, 1)
        viewNode.SetBoxVisible(False)
        viewNode.SetAxisLabelsVisible(False)

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
        self.ui.shot1Button.enabled = canRun
        self.ui.shot2Button.enabled = canRun
        self.ui.shot3Button.enabled = canRun
        self.ui.shot1ButtonAgain.enabled = canRun
        self.ui.shot2ButtonAgain.enabled = canRun
        self.ui.shot3ButtonAgain.enabled = canRun
        self.ui.shot1ButtonShow.enabled = True
        self.ui.shot2ButtonShow.enabled = True
        self.ui.shot3ButtonShow.enabled = True
        self.ui.shot1Button.toolTip = _("Select input volume" if not canRun else "Capture shot")

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

    def onShowMarkerButton(self):
        markerModelNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode")
        markerModelNode.SetName("markers")
        markerModelNode.SetAndObservePolyData(self.markerSource)

        displayNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelDisplayNode")
        displayNode.SetColor(0, 0, 0)
        markerModelNode.SetAndObserveDisplayNodeID(displayNode.GetID())

    def onShot1Button(self):
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("需要先加载 Volume 并点击 showMarker 生成 markers")
            return

        bodyVolumeNode.SetDisplayVisibility(True)
        markerModelNode.SetDisplayVisibility(False)
        saveBodyFile = os.path.join(self.savePath, "shot1Body.png")
        self._captureViewToFile(saveBodyFile)


    def onShot1ButtonAgain(self):
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("需要先加载 Volume 并点击 showMarker 生成 markers")
            return

        bodyVolumeNode.SetDisplayVisibility(False)
        markerModelNode.SetDisplayVisibility(True)
        saveMarkerFile = os.path.join(self.savePath, "shot1Markers.png")
        self._captureViewToFile(saveMarkerFile)


    def onShot1ButtonShow(self):
        saveBodyFile = os.path.join(self.savePath, "shot1Body.png")
        saveMarkerFile = os.path.join(self.savePath, "shot1Markers.png")
        saveNiiFile = os.path.join(self.savePath, "shot1.nii.gz")

        imgBody = self._requireImage(saveBodyFile, "shot1Body")
        imgMarkers = self._requireImage(saveMarkerFile, "shot1Markers")
        if imgBody is None or imgMarkers is None:
            return
        imgBodyGray = cv2.cvtColor(imgBody, cv2.COLOR_BGR2GRAY)
        imgMarkersGray = cv2.cvtColor(imgMarkers, cv2.COLOR_BGR2GRAY)
        imgBodyGrayArr = np.array(imgBodyGray)
        imgMarkersGrayArr = np.array(imgMarkersGray)
        imgMarkersGrayArr = 1000 * ((imgMarkersGrayArr / 255) - 1)  # [-1000, 0]
        imgMarkersGrayArrTMP = (imgMarkersGrayArr + 1000) / 1000

        imgArr = imgBodyGrayArr * imgMarkersGrayArrTMP + imgMarkersGrayArr

        imgITK = sitk.GetImageFromArray(imgArr)
        vtkImage = self.sitk_image_to_vtk_image(imgITK)
        
        writer = vtk.vtkNIFTIImageWriter()
        writer.SetInputData(vtkImage)
        writer.SetFileName(saveNiiFile)
        writer.Update()
        writer.Write()

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
        saveBodyFile = os.path.join(self.savePath, "shot2Body.png")
        self._captureViewToFile(saveBodyFile)


    def onShot2ButtonAgain(self):
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("需要先加载 Volume 并点击 showMarker 生成 markers")
            return

        bodyVolumeNode.SetDisplayVisibility(False)
        markerModelNode.SetDisplayVisibility(True)
        saveMarkerFile = os.path.join(self.savePath, "shot2Markers.png")
        self._captureViewToFile(saveMarkerFile)


    def onShot2ButtonShow(self):
        saveBodyFile = os.path.join(self.savePath, "shot2Body.png")
        saveMarkerFile = os.path.join(self.savePath, "shot2Markers.png")
        saveNiiFile = os.path.join(self.savePath, "shot2.nii.gz")

        imgBody = self._requireImage(saveBodyFile, "shot2Body")
        imgMarkers = self._requireImage(saveMarkerFile, "shot2Markers")
        if imgBody is None or imgMarkers is None:
            return
        imgBodyGray = cv2.cvtColor(imgBody, cv2.COLOR_BGR2GRAY)
        imgMarkersGray = cv2.cvtColor(imgMarkers, cv2.COLOR_BGR2GRAY)
        imgBodyGrayArr = np.array(imgBodyGray)
        imgMarkersGrayArr = np.array(imgMarkersGray)
        imgMarkersGrayArr = 1000 * ((imgMarkersGrayArr / 255) - 1)  # [-1000, 0]
        imgMarkersGrayArrTMP = (imgMarkersGrayArr + 1000) / 1000

        imgArr = imgBodyGrayArr * imgMarkersGrayArrTMP + imgMarkersGrayArr

        imgITK = sitk.GetImageFromArray(imgArr)
        vtkImage = self.sitk_image_to_vtk_image(imgITK)

        writer = vtk.vtkNIFTIImageWriter()
        writer.SetInputData(vtkImage)
        writer.SetFileName(saveNiiFile)
        writer.Update()
        writer.Write()

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
        saveBodyFile = os.path.join(self.savePath, "shot3Body.png")
        self._captureViewToFile(saveBodyFile)

    def onShot3ButtonAgain(self):
        bodyVolumeNode = self._getBodyVolumeNode()
        markerModelNode = self._getMarkersModelNode()
        if not bodyVolumeNode or not markerModelNode:
            self._error("需要先加载 Volume 并点击 showMarker 生成 markers")
            return

        bodyVolumeNode.SetDisplayVisibility(False)
        markerModelNode.SetDisplayVisibility(True)
        saveMarkerFile = os.path.join(self.savePath, "shot3Markers.png")
        self._captureViewToFile(saveMarkerFile)


    def onShot3ButtonShow(self):
        saveBodyFile = os.path.join(self.savePath, "shot3Body.png")
        saveMarkerFile = os.path.join(self.savePath, "shot3Markers.png")
        saveNiiFile = os.path.join(self.savePath, "shot3.nii.gz")

        imgBody = self._requireImage(saveBodyFile, "shot3Body")
        imgMarkers = self._requireImage(saveMarkerFile, "shot3Markers")
        if imgBody is None or imgMarkers is None:
            return
        imgBodyGray = cv2.cvtColor(imgBody, cv2.COLOR_BGR2GRAY)
        imgMarkersGray = cv2.cvtColor(imgMarkers, cv2.COLOR_BGR2GRAY)
        imgBodyGrayArr = np.array(imgBodyGray)
        imgMarkersGrayArr = np.array(imgMarkersGray)
        imgMarkersGrayArr = 1000 * ((imgMarkersGrayArr / 255) - 1)  # [-1000, 0]
        imgMarkersGrayArrTMP = (imgMarkersGrayArr + 1000) / 1000

        imgArr = imgBodyGrayArr * imgMarkersGrayArrTMP + imgMarkersGrayArr

        imgITK = sitk.GetImageFromArray(imgArr)
        vtkImage = self.sitk_image_to_vtk_image(imgITK)

        writer = vtk.vtkNIFTIImageWriter()
        writer.SetInputData(vtkImage)
        writer.SetFileName(saveNiiFile)
        writer.Update()
        writer.Write()

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

        # 显示点
        markupsNode1 = slicer.mrmlScene.GetFirstNodeByName("markers1")
        if markupsNode1 == None:
            markupsNode1 = slicer.vtkMRMLMarkupsFiducialNode()
            markupsNode1.SetName("markers1")
            slicer.mrmlScene.AddNode(markupsNode1)
            markupsNode1.CreateDefaultDisplayNodes()
            displayNode1 = markupsNode1.GetDisplayNode()
            displayNode1.SetVisibility(True)
            displayNode1.SetViewNodeIDs(["vtkMRMLSliceNodeRed"])
            displayNode1.SetVisibility3D(False)
            displayNode1.SetGlyphScale(0.15)
            displayNode1.SetSelectedColor([1, 0, 0])

            for p in self.bigMarkersSort1.keys():
                n = markupsNode1.AddControlPoint(p)
                mark = str(self.bigMarkersSort1[p])
                markupsNode1.SetNthControlPointLabel(n, mark)
            for p in self.smallMarkersSort1.keys():
                n = markupsNode1.AddControlPoint(p)
                mark = str(self.smallMarkersSort1[p])
                markupsNode1.SetNthControlPointLabel(n, mark)
        else:
            for i, p in enumerate(self.bigMarkersSort1.keys()):
                markupsNode1.SetNthControlPointPosition(i, p)
            for i, p in enumerate(self.smallMarkersSort1.keys()):
                markupsNode1.SetNthControlPointPosition(len(self.bigMarkersSort1.keys())+i, p)
        
        markupsNode2 = slicer.mrmlScene.GetFirstNodeByName("markers2")
        if markupsNode2 == None:
            markupsNode2 = slicer.vtkMRMLMarkupsFiducialNode()
            markupsNode2.SetName("markers2")
            slicer.mrmlScene.AddNode(markupsNode2)
            markupsNode2.CreateDefaultDisplayNodes()
            displayNode2 = markupsNode2.GetDisplayNode()
            displayNode2.SetVisibility(True)
            displayNode2.SetViewNodeIDs(["vtkMRMLSliceNodeGreen"])
            displayNode2.SetVisibility3D(False)
            displayNode2.SetGlyphScale(0.15)
            displayNode2.SetSelectedColor([0, 1, 0])

            for p in self.bigMarkersSort2.keys():
                n = markupsNode2.AddControlPoint(p)
                mark = str(self.bigMarkersSort2[p])
                markupsNode2.SetNthControlPointLabel(n, mark)
            for p in self.smallMarkersSort2.keys():
                n = markupsNode2.AddControlPoint(p)
                mark = str(self.smallMarkersSort2[p])
                markupsNode2.SetNthControlPointLabel(n, mark)
        else:
            for i, p in enumerate(self.bigMarkersSort2.keys()):
                markupsNode2.SetNthControlPointPosition(i, p)
            for i, p in enumerate(self.smallMarkersSort2.keys()):
                markupsNode2.SetNthControlPointPosition(len(self.bigMarkersSort2.keys())+i, p)
        
        markupsNode3 = slicer.mrmlScene.GetFirstNodeByName("markers3")
        if markupsNode3 == None:
            markupsNode3 = slicer.vtkMRMLMarkupsFiducialNode()
            markupsNode3.SetName("markers3")  
            slicer.mrmlScene.AddNode(markupsNode3)
            markupsNode3.CreateDefaultDisplayNodes()
            displayNode3 = markupsNode3.GetDisplayNode()
            displayNode3.SetVisibility(True)
            displayNode3.SetViewNodeIDs(["vtkMRMLSliceNodeYellow"])
            displayNode3.SetVisibility3D(False)
            displayNode3.SetGlyphScale(0.15)
            displayNode3.SetSelectedColor([0, 0, 1])
        
            for p in self.bigMarkersSort3.keys():
                n = markupsNode3.AddControlPoint(p)
                mark = str(self.bigMarkersSort3[p])
                markupsNode3.SetNthControlPointLabel(n, mark)
            for p in self.smallMarkersSort3.keys():
                n = markupsNode3.AddControlPoint(p)
                mark = str(self.smallMarkersSort3[p])
                markupsNode3.SetNthControlPointLabel(n, mark)
        else:
            for i, p in enumerate(self.bigMarkersSort3.keys()):
                markupsNode3.SetNthControlPointPosition(i, p)
            for i, p in enumerate(self.smallMarkersSort3.keys()):
                markupsNode3.SetNthControlPointPosition(len(self.bigMarkersSort3.keys())+i, p)

        # 编排所有marker顺序
        self.initMarkers()  
        # 并且计算 3 个平行光向量
        self.initLightVec()

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
        markupsNode1 = slicer.vtkMRMLMarkupsFiducialNode()
        markupsNode1.SetName("markers3D1")
        markupsNode2 = slicer.vtkMRMLMarkupsFiducialNode()
        markupsNode2.SetName("markers3D2")
        markupsNode3 = slicer.vtkMRMLMarkupsFiducialNode()
        markupsNode3.SetName("markers3D3")

        slicer.mrmlScene.AddNode(markupsNode1)
        slicer.mrmlScene.AddNode(markupsNode2)
        slicer.mrmlScene.AddNode(markupsNode3)

        markupsNode1.CreateDefaultDisplayNodes()
        markupsNode2.CreateDefaultDisplayNodes()
        markupsNode3.CreateDefaultDisplayNodes()

        displayNode1 = markupsNode1.GetDisplayNode()
        displayNode2 = markupsNode2.GetDisplayNode()
        displayNode3 = markupsNode3.GetDisplayNode()

        displayNode1.SetVisibility(False)
        displayNode2.SetVisibility(False)
        displayNode3.SetVisibility(False)

        displayNode1.SetVisibility3D(True)
        displayNode2.SetVisibility3D(True)
        displayNode3.SetVisibility3D(True)

        displayNode1.SetGlyphScale(0.15)
        displayNode2.SetGlyphScale(0.15)
        displayNode3.SetGlyphScale(0.15)

        displayNode1.SetSelectedColor([1, 0, 0])
        displayNode2.SetSelectedColor([0, 1, 0])
        displayNode3.SetSelectedColor([0, 0, 1])

        for k in self.bigMarker3DDic1.keys():
            p = self.bigMarker3DDic1[k]
            n = markupsNode1.AddControlPoint(p)
            mark = str(k)
            markupsNode1.SetNthControlPointLabel(n, mark)
        for k in self.smallMarker3DDic1.keys():
            p = self.smallMarker3DDic1[k]
            n = markupsNode1.AddControlPoint(p)
            mark = str(k)
            markupsNode1.SetNthControlPointLabel(n, mark)

        for k in self.bigMarker3DDic2.keys():
            p = self.bigMarker3DDic2[k]
            n = markupsNode2.AddControlPoint(p)
            mark = str(k)
            markupsNode2.SetNthControlPointLabel(n, mark)
        for k in self.smallMarker3DDic2.keys():
            p = self.smallMarker3DDic2[k]
            n = markupsNode2.AddControlPoint(p)
            mark = str(k)
            markupsNode2.SetNthControlPointLabel(n, mark)

        for k in self.bigMarker3DDic3.keys():
            p = self.bigMarker3DDic3[k]
            n = markupsNode3.AddControlPoint(p)
            mark = str(k)
            markupsNode3.SetNthControlPointLabel(n, mark)
        for k in self.smallMarker3DDic3.keys():
            p = self.smallMarker3DDic3[k]
            n = markupsNode3.AddControlPoint(p)
            mark = str(k)
            markupsNode3.SetNthControlPointLabel(n, mark)

        
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

        # *******************投影变换*******************
        # 使用出厂设置的 marker 坐标 4个点
        # -----------------------------------------------------------------
        originBigMarker3DDic = GenerateMarkers().bigMarker3DDic
        originSmallMarker3DDic = GenerateMarkers().smallMarker3DDic
        originBigMarker3D_4points = np.array([
            originBigMarker3DDic[2][0:2], 
            originBigMarker3DDic[3][0:2], 
            originBigMarker3DDic[4][0:2], 
            originBigMarker3DDic[5][0:2]
        ], dtype=np.float32)
        originSmallMarker3D_4points = np.array([
            originSmallMarker3DDic[2][0:2],
            originSmallMarker3DDic[3][0:2],
            originSmallMarker3DDic[4][0:2],
            originSmallMarker3DDic[5][0:2]
        ], dtype=np.float32)

        # 2D 图像中的像素坐标
        # 这是第一个视图
        big_2DMarker1_source_4points = np.array([
            get_key_by_value(self.bigMarkersSort1, 2)[0:2], 
            get_key_by_value(self.bigMarkersSort1, 3)[0:2], 
            get_key_by_value(self.bigMarkersSort1, 4)[0:2], 
            get_key_by_value(self.bigMarkersSort1, 5)[0:2], 
        ], dtype=np.float32)

        small_2DMarker1_source_4points = np.array([
            get_key_by_value(self.smallMarkersSort1, 2)[0:2], 
            get_key_by_value(self.smallMarkersSort1, 3)[0:2], 
            get_key_by_value(self.smallMarkersSort1, 4)[0:2], 
            get_key_by_value(self.smallMarkersSort1, 5)[0:2], 
        ], dtype=np.float32)

        self.M3D2DPerspectiveMatrixsBig1 = self.logic.getPerspectiveTransform(originBigMarker3D_4points, big_2DMarker1_source_4points)
        self.M2D3DPerspectiveMatrixsBig1 = self.logic.getPerspectiveTransform(big_2DMarker1_source_4points, originBigMarker3D_4points)
        self.M3D2DPerspectiveMatrixsSmall1 = self.logic.getPerspectiveTransform(originSmallMarker3D_4points, small_2DMarker1_source_4points)
        self.M2D3DPerspectiveMatrixsSmall1 = self.logic.getPerspectiveTransform(small_2DMarker1_source_4points, originSmallMarker3D_4points)

        # 这是第二个视图
        big_2DMarker2_source_4points = np.array([
            get_key_by_value(self.bigMarkersSort2, 2)[0:2], 
            get_key_by_value(self.bigMarkersSort2, 3)[0:2], 
            get_key_by_value(self.bigMarkersSort2, 4)[0:2], 
            get_key_by_value(self.bigMarkersSort2, 5)[0:2], 
        ], dtype=np.float32)

        small_2DMarker2_source_4points = np.array([
            get_key_by_value(self.smallMarkersSort2, 2)[0:2], 
            get_key_by_value(self.smallMarkersSort2, 3)[0:2], 
            get_key_by_value(self.smallMarkersSort2, 4)[0:2], 
            get_key_by_value(self.smallMarkersSort2, 5)[0:2], 
        ], dtype=np.float32)

        self.M3D2DPerspectiveMatrixsBig2 = self.logic.getPerspectiveTransform(originBigMarker3D_4points, big_2DMarker2_source_4points)
        self.M2D3DPerspectiveMatrixsBig2 = self.logic.getPerspectiveTransform(big_2DMarker2_source_4points, originBigMarker3D_4points)
        self.M3D2DPerspectiveMatrixsSmall2 = self.logic.getPerspectiveTransform(originSmallMarker3D_4points, small_2DMarker2_source_4points)
        self.M2D3DPerspectiveMatrixsSmall2 = self.logic.getPerspectiveTransform(small_2DMarker2_source_4points, originSmallMarker3D_4points)

        # 这是第三个视图
        big_2DMarker3_source_4points = np.array([
            get_key_by_value(self.bigMarkersSort3, 2)[0:2], 
            get_key_by_value(self.bigMarkersSort3, 3)[0:2], 
            get_key_by_value(self.bigMarkersSort3, 4)[0:2], 
            get_key_by_value(self.bigMarkersSort3, 5)[0:2], 
        ], dtype=np.float32)

        small_2DMarker3_source_4points = np.array([
            get_key_by_value(self.smallMarkersSort3, 2)[0:2], 
            get_key_by_value(self.smallMarkersSort3, 3)[0:2], 
            get_key_by_value(self.smallMarkersSort3, 4)[0:2], 
            get_key_by_value(self.smallMarkersSort3, 5)[0:2], 
        ], dtype=np.float32)

        self.M3D2DPerspectiveMatrixsBig3 = self.logic.getPerspectiveTransform(originBigMarker3D_4points, big_2DMarker3_source_4points)
        self.M2D3DPerspectiveMatrixsBig3 = self.logic.getPerspectiveTransform(big_2DMarker3_source_4points, originBigMarker3D_4points)
        self.M3D2DPerspectiveMatrixsSmall3 = self.logic.getPerspectiveTransform(originSmallMarker3D_4points, small_2DMarker3_source_4points)
        self.M2D3DPerspectiveMatrixsSmall3 = self.logic.getPerspectiveTransform(small_2DMarker3_source_4points, originSmallMarker3D_4points)


    def initLightVec(self):
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

        bigIntersectionP3D = self.logic.line2plane_intersection(line_p1, line_p2, bigPlane_p1, bigPlane_p2, bigPlane_p3)
        smallIntersectionP3D = self.logic.line2plane_intersection(line_p1, line_p2, smallPlane_p1, smallPlane_p2, smallPlane_p3)

        # Debug visualization: Red ray and intersections
        if self.debugVisualization:
            # Red ray line (from big to small plane)
            self._createOrUpdateVisualizationNode(
                "vis_RedRay", 
                nodeType="MarkupsLine",
                color=(1, 0, 0),  # Red
                linePoints=[self.p3DBigRed, self.p3DSmallRed]
            )
            
            # Intersection points
            if bigIntersectionP3D is not None:
                self._createOrUpdateVisualizationNode(
                    "vis_RedBigIntersection",
                    position=bigIntersectionP3D,
                    color=(1, 0.5, 0.5),  # Light red
                    nodeType="MarkupsFiducial"
                )
            
            if smallIntersectionP3D is not None:
                self._createOrUpdateVisualizationNode(
                    "vis_RedSmallIntersection",
                    position=smallIntersectionP3D,
                    color=(1, 0.5, 0.5),  # Light red
                    nodeType="MarkupsFiducial"
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
            
        # 计算手动添加的点到直线的最近点，将点自动移动到直线上
        p3DNearest2Line = self.logic.pointNearest2Line(p3D, lineP1, lineP2)
        # 替换手动添加的 markupNode
        markupNode.SetNthControlPointPosition(0, p3DNearest2Line)
        p2DNearest2Line = p3DNearest2Line[0:2]

        self.p3DBigGreen = self.logic.twoD2threeD(p2DNearest2Line, self.M2D3DPerspectiveMatrixsBig2, self.M2D3DRigidMatrixsBig2, self.originBigMarker3D_Z)
        self.p3DSmallGreen = self.logic.twoD2threeD(p2DNearest2Line, self.M2D3DPerspectiveMatrixsSmall2, self.M2D3DRigidMatrixsSmall2, self.originSmallMarker3D_Z)

        # Debug visualization: Green ray
        if self.debugVisualization:
            # Green ray line (from big to small plane)
            self._createOrUpdateVisualizationNode(
                "vis_GreenRay",
                nodeType="MarkupsLine",
                color=(0, 1, 0),  # Green
                linePoints=[self.p3DBigGreen, self.p3DSmallGreen]
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
        if line_gap is not None:
            self.ui.lineGapDisplay.setText(f"{line_gap:.2f} mm")
            logging.info(f"Ray gap (closest distance): {line_gap:.4f} mm")
        
        # Debug visualization: TargetP3D calculation steps
        if self.debugVisualization:
            # Ray lines if not already shown
            if not slicer.mrmlScene.GetFirstNodeByName("vis_RedRay"):
                self._createOrUpdateVisualizationNode(
                    "vis_RedRay",
                    nodeType="MarkupsLine",
                    color=(1, 0, 0),  # Red
                    linePoints=[line1_p1, line1_p2]
                )
            if not slicer.mrmlScene.GetFirstNodeByName("vis_GreenRay"):
                self._createOrUpdateVisualizationNode(
                    "vis_GreenRay",
                    nodeType="MarkupsLine",
                    color=(0, 1, 0),  # Green
                    linePoints=[line2_p1, line2_p2]
                )
            
            # TargetP3D midpoint
            self._createOrUpdateVisualizationNode(
                "vis_TargetP3DMidpoint",
                position=p3D,
                color=(1, 1, 0),  # Yellow
                nodeType="MarkupsFiducial"
            )

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

    def onDebugVisToggle(self, enabled):
        """Toggle visibility of all debug visualization nodes"""
        self.debugVisualization = enabled
        
        # Toggle visibility of all visualization nodes if they exist
        visNodeNames = [
            "vis_RedRay", "vis_GreenRay", "vis_RedGreenIntersections",
            "vis_GreenReprojected", "vis_TargetP3DRay1", "vis_TargetP3DRay2",
            "vis_TargetP3DMidpoint", "vis_TargetP3DStep"
        ]
        
        for nodeName in visNodeNames:
            node = slicer.mrmlScene.GetFirstNodeByName(nodeName)
            if node:
                displayNode = node.GetDisplayNode()
                if displayNode:
                    displayNode.SetVisibility(enabled)

    def _createOrUpdateVisualizationNode(self, nodeName, position, color=(1, 1, 1), 
                                        nodeType="MarkupsFiducial", linePoints=None):
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
            node.SetNthMarkupAssignedID(0, nodeName)
            
            # Set display properties
            displayNode = node.GetDisplayNode()
            if not displayNode:
                displayNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsDisplayNode")
                node.SetAndObserveDisplayNodeID(displayNode.GetID())
            
            displayNode.SetGlyphScale(2.0)
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



