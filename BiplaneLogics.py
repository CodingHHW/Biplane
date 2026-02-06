#
# BiplaneLogic
#
import logging

import vtk
from typing import Annotated, Optional
import slicer
from slicer.ScriptedLoadableModule import *
from slicer import vtkMRMLScalarVolumeNode

import SimpleITK as sitk
# Ensure OpenCV is available; install on demand for Slicer environment
try:
    import cv2
except ImportError:  # pragma: no cover - runtime install path
    slicer.util.pip_install("opencv-python")
    import cv2
import numpy as np
import copy
from slicer.parameterNodeWrapper import (
    parameterNodeWrapper,
    WithinRange,
)


def get_key_by_value(dictionary, value):
    for key, val in dictionary.items():
        if val == value:
            return key
    return None


@parameterNodeWrapper
class BiplaneParameterNode:
    """
    The parameters needed by module.

    inputVolume - The volume to threshold.
    imageThreshold - The value at which to threshold the input volume.
    invertThreshold - If true, will invert the threshold.
    thresholdedVolume - The output volume that will contain the thresholded volume.
    invertedVolume - The output volume that will contain the inverted thresholded volume.
    """

    inputVolume: Optional[vtkMRMLScalarVolumeNode] = None
    imageThreshold: Annotated[float, WithinRange(-100, 500)] = 100
    invertThreshold: bool = False
    thresholdedVolume: Optional[vtkMRMLScalarVolumeNode] = None
    invertedVolume: Optional[vtkMRMLScalarVolumeNode] = None


class GenerateMarkers():

    def __init__(self) -> None:
        bigSphereSources = []
        bigCenter = 40
        smallCenter = 20
        self.bigRadius = 6
        self.smallRadius = 4

        self.bigMarker3DDic = {}
        self.smallMarker3DDic = {}

        self.bigMarker3DDic[1] = (-bigCenter, bigCenter, 0)
        self.bigMarker3DDic[2] = (-bigCenter, -bigCenter, 0)
        self.bigMarker3DDic[3] = (bigCenter, -bigCenter, 0)
        self.bigMarker3DDic[4] = (bigCenter, bigCenter, 0)
        self.bigMarker3DDic[5] = (-bigCenter - 15, bigCenter, 0)

        self.smallMarker3DDic[1] = (smallCenter, -smallCenter, smallCenter + 2)
        self.smallMarker3DDic[2] = (smallCenter, smallCenter, smallCenter + 2)
        self.smallMarker3DDic[3] = (-smallCenter, smallCenter, smallCenter + 2)
        self.smallMarker3DDic[4] = (-smallCenter, -smallCenter, smallCenter + 2)
        self.smallMarker3DDic[5] = (6, -smallCenter, smallCenter + 2)

        # print("-----------------")
        for i in range(4):
            bigSphereSources.append(vtk.vtkSphereSource())
            bigSphereSources[i].SetCenter((-1) ** i * bigCenter, (-1) ** (i // 2) * bigCenter, 0)
            bigSphereSources[i].SetRadius(self.bigRadius)
            bigSphereSources[i].SetThetaResolution(100)
            bigSphereSources[i].SetPhiResolution(100)
            bigSphereSources[i].Update()
            # print(bigSphereSources[i].GetCenter())

        # print("------------------")
        smallSphereSources = []
        for i in range(4):
            smallSphereSources.append(vtk.vtkSphereSource())
            smallSphereSources[i].SetCenter((-1) ** i * smallCenter, (-1) ** (i // 2) * smallCenter, smallCenter + 2)
            smallSphereSources[i].SetRadius(self.smallRadius)
            smallSphereSources[i].SetThetaResolution(100)
            smallSphereSources[i].SetPhiResolution(100)
            smallSphereSources[i].Update()
            # print(smallSphereSources[i].GetCenter())

        bigOtherSphereSource = vtk.vtkSphereSource()
        bigOtherSphereSource.SetCenter(-bigCenter - 15, bigCenter, 0)
        bigOtherSphereSource.SetRadius(self.bigRadius)
        bigOtherSphereSource.SetThetaResolution(100)
        bigOtherSphereSource.SetPhiResolution(100)
        bigOtherSphereSource.Update()
        # print(bigOtherSphereSource.GetCenter())

        smallOtherSphereSource = vtk.vtkSphereSource()
        smallOtherSphereSource.SetCenter(6, -smallCenter, smallCenter + 2)
        smallOtherSphereSource.SetRadius(self.smallRadius)
        smallOtherSphereSource.SetThetaResolution(100)
        smallOtherSphereSource.SetPhiResolution(100)
        smallOtherSphereSource.Update()
        # print(smallOtherSphereSource.GetCenter())

        append = vtk.vtkAppendPolyData()
        for i in range(4):
            append.AddInputData(bigSphereSources[i].GetOutput())
        for i in range(4):
            append.AddInputData(smallSphereSources[i].GetOutput())
        append.AddInputData(bigOtherSphereSource.GetOutput())
        append.AddInputData(smallOtherSphereSource.GetOutput())
        append.Update()
        self.marksSource = append.GetOutput()

    def getMarkerTransform(self, transformNode, marker3DDic):
        resMarker3DDic = {}

        transformMatrix = vtk.vtkMatrix4x4()
        transformNode.GetMatrixTransformToParent(transformMatrix)

        transform = vtk.vtkTransform()
        transform.SetMatrix(transformMatrix)

        for k in marker3DDic.keys():
            p = marker3DDic[k]
            resMarker3DDic[k] = transform.TransformPoint([p[0], p[1], p[2]])
        
        return resMarker3DDic


    def getMarkerCenters(self, imgITK):

        rs = []
        cs = []
        self.big = []
        self.small = []

        binaryImage = sitk.BinaryThreshold(imgITK, lowerThreshold=0, upperThreshold=3000, insideValue=0, outsideValue=1)
        # sitk.WriteImage(binaryImage, r"/Users/hehongwei/Desktop/biplane_logic.nii.gz")
        label_image = sitk.ConnectedComponent(binaryImage)
        relabel_image = sitk.RelabelComponent(label_image)
        statistics_filter = sitk.LabelShapeStatisticsImageFilter()
        statistics_filter.Execute(relabel_image)
        for label in statistics_filter.GetLabels():
            r = statistics_filter.GetBoundingBox(label)[-2]
            c = statistics_filter.GetCentroid(label)
            rs.append(r)
            cs.append(c)
        # print(rs)
        min_r = np.min(rs)
        max_r = np.max(rs)
        threshold = (min_r + max_r) / 2
        # print(min_r, max_r, threshold)
        for i in range(len(cs)):
            r = rs[i]
            c = cs[i]
            if r > threshold:
                self.big.append(c)
            else:
                self.small.append(c)
        # print("----------------big-----------------")
        # print(self.big)
        # print("----------------small-----------------")
        # print(self.small)

    def bigMarkersSort(self):
        bigMarkerDic = {}
        bigTmp = copy.deepcopy(self.big)
        for i in range(5):
            p1 = self.big[i]
            x1, y1 = p1[0], p1[1]
            vs = []
            for j in range(5):
                if i == j:
                    continue
                p2 = self.big[j]
                x2, y2 = p2[0], p2[1]
                v = np.array([x2 - x1, y2 - y1])
                vs.append(v)

            for iv in range(len(vs)):
                v1 = vs[iv]
                for jv in range(iv + 1, len(vs)):
                    v2 = vs[jv]
                    angle = np.arccos(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))
                    l1 = np.linalg.norm(v1)
                    l2 = np.linalg.norm(v2)
                    # print(l1, l2)
                    # print(np.degrees(angle))
                    if abs(np.degrees(angle) - 180) < 1:
                        bigMarkerDic[p1] = 1
                        bigTmp.remove(p1)
                    if np.degrees(angle) < 1 and abs(l1 - l2) > min(l1, l2):
                        bigMarkerDic[p1] = 5
                        bigTmp.remove(p1)
                    if np.degrees(angle) < 1 and abs(l1 - l2) < min(l1, l2):
                        bigMarkerDic[p1] = 4
                        bigTmp.remove(p1)

        # print(bigMarkerDic)
        p4 = get_key_by_value(bigMarkerDic, 4)
        p1 = get_key_by_value(bigMarkerDic, 1)
        v41 = np.array([p1[0] - p4[0], p1[1] - p4[1]])

        pp1 = bigTmp[0]
        pp2 = bigTmp[1]
        vpp = np.array([pp2[0] - pp1[0], pp2[1] - pp1[1]])
        angle = np.arccos(np.dot(v41, vpp) / (np.linalg.norm(v41) * np.linalg.norm(vpp)))
        # print(np.degrees(angle))
        if abs(np.degrees(angle)) < 0.5:
            bigMarkerDic[pp1] = 3
            bigMarkerDic[pp2] = 2
        else:
            bigMarkerDic[pp1] = 2
            bigMarkerDic[pp2] = 3

        return bigMarkerDic

    def smallMarkersSort(self):
        smallMarkerDic = {}
        smallTmp = copy.deepcopy(self.small)
        for i in range(5):
            # print("-----------------")
            p1 = self.small[i]
            x1, y1 = p1[0], p1[1]
            vs = []
            for j in range(5):
                if i == j:
                    continue
                p2 = self.small[j]
                x2, y2 = p2[0], p2[1]
                v = np.array([x2 - x1, y2 - y1])
                vs.append(v)

            for iv in range(len(vs)):
                v1 = vs[iv]
                for jv in range(iv + 1, len(vs)):
                    v2 = vs[jv]
                    angle = np.arccos(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))
                    l1 = np.linalg.norm(v1)
                    l2 = np.linalg.norm(v2)
                    # print(np.degrees(angle))
                    if abs(np.degrees(angle) - 180) < 1:
                        smallMarkerDic[p1] = 5
                        smallTmp.remove(p1)
                    if np.degrees(angle) < 1 and abs(l1 - l2) > min(l1, l2):
                        smallMarkerDic[p1] = 1
                        smallTmp.remove(p1)
                    if np.degrees(angle) < 1 and abs(l1 - l2) < min(l1, l2):
                        smallMarkerDic[p1] = 4
                        smallTmp.remove(p1)

        # print(smallMarkerDic)
        p4 = get_key_by_value(smallMarkerDic, 4)
        p1 = get_key_by_value(smallMarkerDic, 1)
        v41 = np.array([p1[0] - p4[0], p1[1] - p4[1]])
        # print(smallTmp)

        pp1 = smallTmp[0]
        pp2 = smallTmp[1]
        vpp = np.array([pp2[0] - pp1[0], pp2[1] - pp1[1]])
        angle = np.arccos(np.dot(v41, vpp) / (np.linalg.norm(v41) * np.linalg.norm(vpp)))
        if abs(np.degrees(angle)) < 0.5:
            smallMarkerDic[pp1] = 3
            smallMarkerDic[pp2] = 2
        else:
            smallMarkerDic[pp1] = 2
            smallMarkerDic[pp2] = 3

        return smallMarkerDic

    def move2slicer(self, markersSort):
        resDic = {}
        for key in markersSort.keys():
            value = markersSort[key]
            resDic[(-key[0], -key[1], 0)] = value
        return resDic

class BiplaneLogic(ScriptedLoadableModuleLogic):
    """This class should implement all the actual
    computation done by your module.  The interface
    should be such that other python code can import
    this class and make use of the functionality without
    requiring an instance of the Widget.
    Uses ScriptedLoadableModuleLogic base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self) -> None:
        """Called when the logic class is instantiated. Can be used for initializing member variables."""
        ScriptedLoadableModuleLogic.__init__(self)

    def getParameterNode(self):
        return BiplaneParameterNode(super().getParameterNode())

    def getRigidMatrix(self, source_points: np.array, target_points: np.array):
        # 计算质心
        source_center = np.mean(source_points, axis=0)
        target_center = np.mean(target_points, axis=0)

        # 中心化点云
        source_points_centered = source_points - source_center
        target_points_centered = target_points - target_center

        # 计算协方差矩阵
        covariance_matrix = np.dot(source_points_centered.T, target_points_centered)

        # 使用奇异值分解计算旋转矩阵（避免 SciPy 依赖，使用 NumPy）
        U, _, Vt = np.linalg.svd(covariance_matrix)
        rotation_matrix = np.dot(Vt.T, U.T)

        # 处理反射情况，确保是 proper rotation（det=+1）
        if np.linalg.det(rotation_matrix) < 0:
            Vt[-1, :] *= -1
            rotation_matrix = np.dot(Vt.T, U.T)

        # 计算平移向量
        translation_vector = target_center - np.dot(rotation_matrix, source_center)

        return rotation_matrix, translation_vector
    
    def getRigidTargetPoint(self, rotation_matrix, translation_vector, source_points):
        transformed_source_points = np.dot(rotation_matrix, source_points.T).T + translation_vector
        return transformed_source_points

    def getPerspectiveTransform(self, source_points: np.array, target_points: np.array):
        M = cv2.getPerspectiveTransform(source_points, target_points)
        return M
    
    def getPerspectiveTargetPoint(self, perspective_matrix, source_points: np.array):
        target_points = cv2.perspectiveTransform(source_points.reshape(-1, 1, 2), perspective_matrix)
        return target_points.reshape(-1, 2)[0]
    
    def twoD2threeD(self, p2D, M2D3DPerspectiveMatrixs, M2D3DRigidMatrixs, originMarker3D_Z):
        tmpP2D = self.getPerspectiveTargetPoint(M2D3DPerspectiveMatrixs, p2D)
        tmpP3D = np.array([tmpP2D[0], tmpP2D[1], originMarker3D_Z])
        p3D = self.getRigidTargetPoint(M2D3DRigidMatrixs[0], M2D3DRigidMatrixs[1], tmpP3D)
        return p3D

    def threeD2twoD(self, p3D, M3D2DRigidMatrixs, M3D2DPerspectiveMatrixs):
        tmpP3D = self.getRigidTargetPoint(M3D2DRigidMatrixs[0], M3D2DRigidMatrixs[1], p3D)
        tmpP2D = tmpP3D[0:2]
        p2D = self.getPerspectiveTargetPoint(M3D2DPerspectiveMatrixs, tmpP2D)
        return p2D

    def threeD2twoDFor3DSpace(self, p3D: np.array, spaceVec: np.array, 
                              plane_p1: np.array, plane_p2: np.array, plane_p3: np.array, 
                              M3D2DRigidMatrixs, M3D2DPerspectiveMatrixs):
        p3DLine_p1 = p3D
        p3DLine_p2 = p3D + spaceVec

        intersectionP3D = self.line2plane_intersection(p3DLine_p1, p3DLine_p2, plane_p1, plane_p2, plane_p3)
        intersectionP2D = self.threeD2twoD(intersectionP3D, M3D2DRigidMatrixs, M3D2DPerspectiveMatrixs)
        return intersectionP2D

    def line2line_intersection(self, line1_p1: np.array, line1_p2: np.array, line2_p1: np.array, line2_p2: np.array):
        # 计算直线1的斜率和截距
        if line1_p2[0] == line1_p1[0]:
            slope1 = np.inf
            intercept1 = line1_p1[0]
        else:
            slope1 = (line1_p2[1] - line1_p1[1]) / (line1_p2[0] - line1_p1[0])
            intercept1 = line1_p1[1] - slope1 * line1_p1[0]
        
        # 计算直线2的斜率和截距
        if line2_p2[0] == line2_p1[0]:
            slope2 = np.inf
            intercept2 = line2_p1[0]
        else:
            slope2 = (line2_p2[1] - line2_p1[1]) / (line2_p2[0] - line2_p1[0])
            intercept2 = line2_p1[1] - slope2 * line2_p1[0]
        
        # 判断直线是否平行
        if (np.isinf(slope1) and np.isinf(slope2)) or np.isclose(slope1, slope2):
            return None  # 无交点
        
        # 计算交点的坐标
        if np.isinf(slope1):
            x = intercept1
            y = slope2 * x + intercept2
        elif np.isinf(slope2):
            x = intercept2
            y = slope1 * x + intercept1
        else:
            x = (intercept2 - intercept1) / (slope1 - slope2)
            y = slope1 * x + intercept1
        
        return np.array([x, y])


    def line2line_intersection3D(self, line1_p1: np.array, line1_p2: np.array, line2_p1: np.array, line2_p2: np.array):
        # 计算直线1的方向向量和点向量
        line1_direction = line1_p2 - line1_p1
        line1_point = line1_p1

        # 计算直线2的方向向量和点向量
        line2_direction = line2_p2 - line2_p1
        line2_point = line2_p1

        # 计算直线1和2的方向向量的叉乘
        cross_product = np.cross(line1_direction, line2_direction)

        # 如果叉乘结果为零，表示直线1和直线2平行或共线，无交点
        if np.allclose(cross_product, [0, 0, 0]):
            return None

        # 计算直线2的参数t
        t = np.dot(np.cross(line2_point - line1_point, line2_direction), cross_product) / np.linalg.norm(cross_product) ** 2

        # 计算交点坐标
        intersection_point = line1_point + line1_direction * t

        return intersection_point

    def line2line_closest_midpoint3D(self, line1_p1: np.array, line1_p2: np.array, line2_p1: np.array, line2_p2: np.array):
        # 计算两条3D直线的最近点中点（适用于斜交直线）
        u = line1_p2 - line1_p1
        v = line2_p2 - line2_p1
        w0 = line1_p1 - line2_p1

        a = np.dot(u, u)
        b = np.dot(u, v)
        c = np.dot(v, v)
        d = np.dot(u, w0)
        e = np.dot(v, w0)

        denom = a * c - b * b
        if np.isclose(denom, 0.0):
            return None, None

        s = (b * e - c * d) / denom
        t = (a * e - b * d) / denom

        p1 = line1_p1 + s * u
        p2 = line2_p1 + t * v
        midpoint = (p1 + p2) * 0.5
        distance = np.linalg.norm(p1 - p2)

        return midpoint, distance

    def isPinLine(self, p: np.array, line_p1: np.array, line_p2: np.array):
        # 判断点是否在线段内
        # p 是待判断的点的坐标
        # line_p1, line_p2 是线段的两个端点坐标
        
        # 计算线段的方向向量
        line_dir = line_p2 - line_p1
        
        # 计算点到线段起点的向量
        p_diff = p - line_p1
        
        # 如果点到线段起点的向量与线段的方向向量的点积小于0，则点在线段的起点的外侧
        if np.dot(p_diff, line_dir) < 0:
            return False
        
        # 如果点到线段起点的向量与线段的方向向量的点积大于线段的长度平方，则点在线段的终点的外侧
        if np.dot(p_diff, line_dir) > np.dot(line_dir, line_dir):
            return False
        
        return True


    def line2plane_intersection(self, line_p1: np.array, line_p2 : np.array, 
                                plane_p1 : np.array, plane_p2 : np.array, plane_p3 : np.array):
        # 构造直线方向向量
        direction = line_p2 - line_p1
        
        # 构造平面的法向量
        normal = np.cross(plane_p2 - plane_p1, plane_p3 - plane_p1)
        
        denom = np.dot(normal, direction)
        if np.isclose(denom, 0.0):
            return None

        # 计算直线与平面的交点
        t = np.dot(normal, plane_p1 - line_p1) / denom
        P = line_p1 + t * direction
        
        return P

    def pointNearest2Line(self, p: np.array, line_p1: np.array, line_p2: np.array):
        # 计算线段的向量
        line_vec = line_p2 - line_p1
        
        # 计算从线段起点到点p的向量
        p_vec = p - line_p1
        
        # 计算线段的长度的平方
        line_length_sq = np.dot(line_vec, line_vec)
        if np.isclose(line_length_sq, 0.0):
            return line_p1
        
        # 计算点p在线段上的投影长度的比例
        t = np.dot(p_vec, line_vec) / line_length_sq
        
        # 如果t小于0，点p在线段p1的左侧，最近点为线段起点p1
        if t < 0:
            nearest_point = line_p1
        
        # 如果t大于1，点p在线段p2的右侧，最近点为线段终点p2
        elif t > 1:
            nearest_point = line_p2
        
        # 否则，点p在线段p1和p2之间，最近点为线段上的某个点
        else:
            nearest_point = line_p1 + t * line_vec
        
        return nearest_point

    def process(self,
                inputVolume: vtkMRMLScalarVolumeNode,
                outputVolume: vtkMRMLScalarVolumeNode,
                imageThreshold: float,
                invert: bool = False,
                showResult: bool = True) -> None:
        """
        Run the processing algorithm.
        Can be used without GUI widget.
        :param inputVolume: volume to be thresholded
        :param outputVolume: thresholding result
        :param imageThreshold: values above/below this threshold will be set to 0
        :param invert: if True then values above the threshold will be set to 0, otherwise values below are set to 0
        :param showResult: show output volume in slice viewers
        """

        if not inputVolume or not outputVolume:
            raise ValueError("Input or output volume is invalid")

        import time

        startTime = time.time()
        logging.info("Processing started")

        # Compute the thresholded output volume using the "Threshold Scalar Volume" CLI module
        cliParams = {
            "InputVolume": inputVolume.GetID(),
            "OutputVolume": outputVolume.GetID(),
            "ThresholdValue": imageThreshold,
            "ThresholdType": "Above" if invert else "Below",
        }
        cliNode = slicer.cli.run(slicer.modules.thresholdscalarvolume, None, cliParams, wait_for_completion=True, update_display=showResult)
        # We don't need the CLI module node anymore, remove it to not clutter the scene with it
        slicer.mrmlScene.RemoveNode(cliNode)

        stopTime = time.time()
        logging.info(f"Processing completed in {stopTime-startTime:.2f} seconds")


# -----------------------------------------------------------------------------
# Compatibility shim:
#
# This repository historically had `BiplaneLogics.py` as a helper module.
# However, if the repo folder is added to Slicer "Additional module paths",
# Slicer attempts to load each top-level `*.py` as a ScriptedLoadableModule.
# It will expect a `class BiplaneLogics(ScriptedLoadableModule)` inside this file.
# Provide a minimal hidden module to avoid instantiation errors.
# -----------------------------------------------------------------------------


class BiplaneLogics(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "BiplaneLogics"
        self.parent.categories = []
        self.parent.dependencies = []
        self.parent.contributors = []
        self.parent.hidden = True
        self.parent.helpText = "Internal helper module (hidden)."
        self.parent.acknowledgementText = ""


class BiplaneLogicsWidget(ScriptedLoadableModuleWidget):
    def setup(self) -> None:
        ScriptedLoadableModuleWidget.setup(self)


class BiplaneLogicsLogic(ScriptedLoadableModuleLogic):
    pass
