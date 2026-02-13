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
import itertools
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
        self.big = []
        self.small = []

        binaryImage = sitk.BinaryThreshold(imgITK, lowerThreshold=-1050, upperThreshold=-950, insideValue=1, outsideValue=0)
        # sitk.WriteImage(binaryImage, r"/Users/hehongwei/Desktop/biplane_logic.nii.gz")
        label_image = sitk.ConnectedComponent(binaryImage)
        relabel_image = sitk.RelabelComponent(label_image)
        statistics_filter = sitk.LabelShapeStatisticsImageFilter()
        statistics_filter.Execute(relabel_image)

        components = []
        for label in statistics_filter.GetLabels():
            centroid = statistics_filter.GetCentroid(label)
            area = float(statistics_filter.GetPhysicalSize(label))
            # Keep only 2D coordinates for downstream logic.
            components.append({
                "center": (float(centroid[0]), float(centroid[1])),
                "area": area,
            })

        if len(components) < 10:
            raise ValueError(f"Detected only {len(components)} marker components, expected at least 10")

        # Keep the 10 largest blobs, then split by area.
        components.sort(key=lambda x: x["area"], reverse=True)
        top10 = components[:10]

        # Big spheres are physically larger; in projection they are expected to occupy larger areas.
        top10.sort(key=lambda x: x["area"], reverse=True)
        big_components = top10[:5]
        small_components = top10[5:]

        self.big = [c["center"] for c in big_components]
        self.small = [c["center"] for c in small_components]

    def _sort_markers_with_template_homography(self, detected_points, template_marker_3d_dic, marker_name: str):
        if len(detected_points) != 5:
            raise ValueError(f"{marker_name}: expected 5 detected points, got {len(detected_points)}")

        labels = sorted(template_marker_3d_dic.keys())
        template_points = np.array(
            [[template_marker_3d_dic[label][0], template_marker_3d_dic[label][1]] for label in labels],
            dtype=np.float64,
        )
        detected = np.array([[p[0], p[1]] for p in detected_points], dtype=np.float64)

        best = None  # (rms, perm)
        for perm in itertools.permutations(range(5)):
            target = detected[list(perm)]
            H, _ = cv2.findHomography(template_points, target, method=0)
            if H is None:
                continue
            projected = cv2.perspectiveTransform(template_points.reshape(-1, 1, 2), H).reshape(-1, 2)
            rms = float(np.sqrt(np.mean(np.sum((projected - target) ** 2, axis=1))))
            if best is None or rms < best[0]:
                best = (rms, perm)

        if best is None:
            raise ValueError(f"{marker_name}: failed to find a valid homography for marker sorting")

        rms, best_perm = best
        logging.info(f"{marker_name} marker sort RMS={rms:.3f}px")

        sorted_markers = {}
        for label, det_idx in zip(labels, best_perm):
            pt = tuple(detected[det_idx].tolist())
            sorted_markers[pt] = label
        return sorted_markers

    def bigMarkersSort(self):
        return self._sort_markers_with_template_homography(
            self.big,
            self.bigMarker3DDic,
            "Big",
        )

    def smallMarkersSort(self):
        return self._sort_markers_with_template_homography(
            self.small,
            self.smallMarker3DDic,
            "Small",
        )

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

    def getPerspectiveTransform(self, source_points: np.array, target_points: np.array, projection_mode: str = "orthographic"):
        src = np.array(source_points, dtype=np.float64)
        tgt = np.array(target_points, dtype=np.float64)

        if projection_mode == "perspective":
            if src.shape[0] < 4:
                raise ValueError("Perspective transform requires at least 4 points")
            homography, _ = cv2.findHomography(src, tgt, method=0)
            if homography is None:
                raise ValueError("Failed to compute perspective homography")
            return homography

        n = src.shape[0]
        src_h = np.hstack([src, np.ones((n, 1))])
        X, _, _, _ = np.linalg.lstsq(src_h, tgt, rcond=None)
        return X.T
    
    def getPerspectiveTargetPoint(self, transform_matrix, source_points: np.array):
        pt = np.array(source_points, dtype=np.float64).flatten()
        pt_h = np.array([pt[0], pt[1], 1.0])
        matrix = np.array(transform_matrix, dtype=np.float64)

        if matrix.shape == (2, 3):
            return matrix @ pt_h

        if matrix.shape == (3, 3):
            mapped = matrix @ pt_h
            if np.isclose(mapped[2], 0.0):
                raise ValueError("Invalid homography mapping: w is zero")
            return mapped[:2] / mapped[2]

        raise ValueError(f"Unsupported transform matrix shape: {matrix.shape}")

    def buildCameraIntrinsics(self, image_width: int, image_height: int, vertical_fov_deg: float):
        width = float(image_width)
        height = float(image_height)
        fov = float(vertical_fov_deg)
        if width <= 0 or height <= 0:
            raise ValueError("Invalid image size for intrinsics")
        if fov <= 0.0 or fov >= 179.0:
            raise ValueError("Invalid vertical FOV for intrinsics")

        fy = (height * 0.5) / np.tan(np.deg2rad(fov * 0.5))
        fx = fy
        cx = (width - 1.0) * 0.5
        cy = (height - 1.0) * 0.5

        camera_matrix = np.array([
            [fx, 0.0, cx],
            [0.0, fy, cy],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)
        dist_coeffs = np.zeros((5, 1), dtype=np.float64)
        return camera_matrix, dist_coeffs

    def estimateCameraPosePnP(self, object_points_3d: np.array, image_points_2d: np.array, camera_matrix: np.array, dist_coeffs: np.array):
        obj = np.array(object_points_3d, dtype=np.float64).reshape(-1, 3)
        img = np.array(image_points_2d, dtype=np.float64).reshape(-1, 2)
        if obj.shape[0] < 4 or img.shape[0] < 4 or obj.shape[0] != img.shape[0]:
            raise ValueError("solvePnP requires matching >= 4 correspondences")

        success, rvec, tvec = cv2.solvePnP(
            obj,
            img,
            np.array(camera_matrix, dtype=np.float64),
            np.array(dist_coeffs, dtype=np.float64),
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not success:
            raise ValueError("cv2.solvePnP failed")
        return rvec, tvec

    def pixelToWorldRay(self, pixel_2d: np.array, camera_matrix: np.array, rvec: np.array, tvec: np.array):
        pixel = np.array([pixel_2d[0], pixel_2d[1], 1.0], dtype=np.float64)
        K = np.array(camera_matrix, dtype=np.float64)
        K_inv = np.linalg.inv(K)
        ray_cam = K_inv @ pixel
        ray_cam = ray_cam / np.linalg.norm(ray_cam)

        R, _ = cv2.Rodrigues(np.array(rvec, dtype=np.float64))
        t = np.array(tvec, dtype=np.float64).reshape(3)

        camera_center_world = -R.T @ t
        ray_dir_world = R.T @ ray_cam
        ray_dir_world = ray_dir_world / np.linalg.norm(ray_dir_world)
        return camera_center_world, ray_dir_world

    def rayPlaneIntersection(self, ray_origin: np.array, ray_dir: np.array, plane_p1: np.array, plane_p2: np.array, plane_p3: np.array):
        origin = np.array(ray_origin, dtype=np.float64)
        direction = np.array(ray_dir, dtype=np.float64)
        p1 = np.array(plane_p1, dtype=np.float64)
        p2 = np.array(plane_p2, dtype=np.float64)
        p3 = np.array(plane_p3, dtype=np.float64)

        normal = np.cross(p2 - p1, p3 - p1)
        denom = np.dot(normal, direction)
        if np.isclose(denom, 0.0):
            return None
        t = np.dot(normal, p1 - origin) / denom
        return origin + t * direction

    def projectPointToImage(self, point_3d: np.array, camera_matrix: np.array, dist_coeffs: np.array, rvec: np.array, tvec: np.array):
        obj = np.array(point_3d, dtype=np.float64).reshape(1, 1, 3)
        image_points, _ = cv2.projectPoints(
            obj,
            np.array(rvec, dtype=np.float64),
            np.array(tvec, dtype=np.float64),
            np.array(camera_matrix, dtype=np.float64),
            np.array(dist_coeffs, dtype=np.float64),
        )
        return image_points.reshape(2)
    
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
