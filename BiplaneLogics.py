"""Biplane 模块的核心几何/投影计算逻辑。

说明：
1. 本文件以“算法与数据处理”为主，不负责 UI 交互。
2. 为方便排错，日志内容保持英文。
3. 本次仅补充中文注释，不改动计算行为。
"""
import logging

import vtk
from typing import Annotated, Optional
import slicer
from slicer.ScriptedLoadableModule import *
from slicer import vtkMRMLScalarVolumeNode

from BiplaneLib.dependencies import import_slicer_dependency

sitk = import_slicer_dependency("SimpleITK", "SimpleITK", install_on_missing=True)
cv2 = import_slicer_dependency("cv2", "opencv-python", install_on_missing=True)
import numpy as np
import itertools
from slicer.parameterNodeWrapper import (
    parameterNodeWrapper,
    WithinRange,
)


def get_key_by_value(dictionary, value):
    """通过字典的 value 反查 key；找不到时返回 None。"""
    for key, val in dictionary.items():
        if val == value:
            return key
    return None


@parameterNodeWrapper
class BiplaneParameterNode:
    """模块参数节点封装。

    当前字段延续自 Slicer Scripted 模块模板：
    1. `inputVolume`: 输入体数据节点
    2. `imageThreshold`: 阈值
    3. `invertThreshold`: 是否反向阈值
    4. `thresholdedVolume` / `invertedVolume`: 输出节点
    """

    inputVolume: Optional[vtkMRMLScalarVolumeNode] = None
    imageThreshold: Annotated[float, WithinRange(-100, 500)] = 100
    invertThreshold: bool = False
    thresholdedVolume: Optional[vtkMRMLScalarVolumeNode] = None
    invertedVolume: Optional[vtkMRMLScalarVolumeNode] = None


class GenerateMarkers():
    """构建模板 marker 几何，并提供 marker 检测后的排序辅助方法。"""

    def __init__(self) -> None:
        """初始化两组 marker 的模板坐标与组合后的 vtkPolyData。"""
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

        # 构造 4 个大球体（规则分布）
        for i in range(4):
            bigSphereSources.append(vtk.vtkSphereSource())
            bigSphereSources[i].SetCenter((-1) ** i * bigCenter, (-1) ** (i // 2) * bigCenter, 0)
            bigSphereSources[i].SetRadius(self.bigRadius)
            bigSphereSources[i].SetThetaResolution(100)
            bigSphereSources[i].SetPhiResolution(100)
            bigSphereSources[i].Update()
        # 构造 4 个小球体（规则分布，z 方向有偏移）
        smallSphereSources = []
        for i in range(4):
            smallSphereSources.append(vtk.vtkSphereSource())
            smallSphereSources[i].SetCenter((-1) ** i * smallCenter, (-1) ** (i // 2) * smallCenter, smallCenter + 2)
            smallSphereSources[i].SetRadius(self.smallRadius)
            smallSphereSources[i].SetThetaResolution(100)
            smallSphereSources[i].SetPhiResolution(100)
            smallSphereSources[i].Update()
        
        # 构造额外的 1 个大球与 1 个小球，用于打破对称性并辅助排序
        bigOtherSphereSource = vtk.vtkSphereSource()
        bigOtherSphereSource.SetCenter(-bigCenter - 15, bigCenter, 0)
        bigOtherSphereSource.SetRadius(self.bigRadius)
        bigOtherSphereSource.SetThetaResolution(100)
        bigOtherSphereSource.SetPhiResolution(100)
        bigOtherSphereSource.Update()

        smallOtherSphereSource = vtk.vtkSphereSource()
        smallOtherSphereSource.SetCenter(6, -smallCenter, smallCenter + 2)
        smallOtherSphereSource.SetRadius(self.smallRadius)
        smallOtherSphereSource.SetThetaResolution(100)
        smallOtherSphereSource.SetPhiResolution(100)
        smallOtherSphereSource.Update()

        # 将 10 个球体拼接为一个 polydata，供 Slicer 模型节点直接显示
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
        """将 marker 模板点应用指定线性变换，返回新的点字典。"""
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
        """在输入图像中提取 marker 连通域中心，并分离 big/small 两组。"""
        self.big = []
        self.small = []

        # 依据灰度范围做二值化（当前数据中 marker 近似在 -1000 附近）
        binaryImage = sitk.BinaryThreshold(imgITK, lowerThreshold=-1050, upperThreshold=-950, insideValue=1, outsideValue=0)

        # 连通域标记并按体素数量重排
        label_image = sitk.ConnectedComponent(binaryImage)
        relabel_image = sitk.RelabelComponent(label_image)
        statistics_filter = sitk.LabelShapeStatisticsImageFilter()
        statistics_filter.Execute(relabel_image)

        components = []
        for label in statistics_filter.GetLabels():
            centroid = statistics_filter.GetCentroid(label)
            area = float(statistics_filter.GetPhysicalSize(label))
            # 后续排序流程只使用 x/y，z 在 2D 截图场景下无意义
            components.append({
                "center": (float(centroid[0]), float(centroid[1])),
                "area": area,
            })

        if len(components) < 10:
            raise ValueError(f"Detected only {len(components)} marker components, expected at least 10")

        # 取面积最大的 10 个连通域（预期正好对应 10 个 marker）
        components.sort(key=lambda x: x["area"], reverse=True)
        top10 = components[:10]

        # 大球的投影面积通常更大，按面积前 5 / 后 5 划分
        top10.sort(key=lambda x: x["area"], reverse=True)
        big_components = top10[:5]
        small_components = top10[5:]

        self.big = [c["center"] for c in big_components]
        self.small = [c["center"] for c in small_components]

    def _sort_markers_with_template_homography(self, detected_points, template_marker_3d_dic, marker_name: str):
        """使用单应矩阵重投影误差（RMS）穷举最佳编号映射。"""
        if len(detected_points) != 5:
            raise ValueError(f"{marker_name}: expected 5 detected points, got {len(detected_points)}")

        labels = sorted(template_marker_3d_dic.keys())
        template_points = np.array(
            [[template_marker_3d_dic[label][0], template_marker_3d_dic[label][1]] for label in labels],
            dtype=np.float64,
        )
        detected = np.array([[p[0], p[1]] for p in detected_points], dtype=np.float64)

        # 穷举 5! 种排列，选择重投影误差最小的一组作为编号对应关系
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
        """对大球 marker 进行编号排序。"""
        return self._sort_markers_with_template_homography(
            self.big,
            self.bigMarker3DDic,
            "Big",
        )

    def smallMarkersSort(self):
        """对小球 marker 进行编号排序。"""
        return self._sort_markers_with_template_homography(
            self.small,
            self.smallMarker3DDic,
            "Small",
        )

    def move2slicer(self, markersSort):
        """将图像坐标转换为 Slicer 切片坐标（x/y 取反）。"""
        resDic = {}
        for key in markersSort.keys():
            value = markersSort[key]
            resDic[(-key[0], -key[1], 0)] = value
        return resDic

class BiplaneLogic(ScriptedLoadableModuleLogic):
    """Biplane 的主算法类。

    目标：
    1. 提供 2D/3D 点变换
    2. 提供相机标定与投影反投影工具
    3. 提供几何求交和距离计算
    """

    def __init__(self) -> None:
        """初始化算法逻辑类。"""
        ScriptedLoadableModuleLogic.__init__(self)

    def getParameterNode(self):
        """返回带类型约束的参数节点。"""
        return BiplaneParameterNode(super().getParameterNode())

    def getRigidMatrix(self, source_points: np.array, target_points: np.array):
        """计算 source 点集到 target 点集的刚体变换（旋转+平移）。"""
        # 1) 分别计算两组点的质心
        source_center = np.mean(source_points, axis=0)
        target_center = np.mean(target_points, axis=0)

        # 2) 点集中心化，消除平移影响
        source_points_centered = source_points - source_center
        target_points_centered = target_points - target_center

        # 3) 计算协方差矩阵
        covariance_matrix = np.dot(source_points_centered.T, target_points_centered)

        # 4) 对协方差矩阵做 SVD，求最优旋转矩阵（Kabsch）
        U, _, Vt = np.linalg.svd(covariance_matrix)
        rotation_matrix = np.dot(Vt.T, U.T)

        # 5) 修正反射情况，保证 det(R)=+1
        if np.linalg.det(rotation_matrix) < 0:
            Vt[-1, :] *= -1
            rotation_matrix = np.dot(Vt.T, U.T)

        # 6) 计算平移向量
        translation_vector = target_center - np.dot(rotation_matrix, source_center)

        return rotation_matrix, translation_vector
    def getRigidTargetPoint(self, rotation_matrix, translation_vector, source_points):
        """将 source_points 应用刚体变换得到目标点。"""
        transformed_source_points = np.dot(rotation_matrix, source_points.T).T + translation_vector
        return transformed_source_points
    def getPerspectiveTransform(self, source_points: np.array, target_points: np.array, projection_mode: str = "orthographic"):
        """根据投影模式计算 2D 映射矩阵。

        - perspective: 返回 3x3 单应矩阵
        - orthographic: 返回 2x3 仿射矩阵
        """
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
        """使用变换矩阵映射二维点坐标。"""
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
        """根据图像尺寸和垂直 FOV 构造相机内参矩阵。"""
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
        """调用 solvePnP 估计相机外参 (rvec, tvec)。"""
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
        """将像素点反投影为世界坐标中的一条射线。"""
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
        """计算射线与平面的交点；若平行则返回 None。"""
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
        """将 3D 点投影到 2D 图像平面。"""
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
        """将 2D 点恢复到 3D（先映射到模板平面，再做刚体变换）。"""
        tmpP2D = self.getPerspectiveTargetPoint(M2D3DPerspectiveMatrixs, p2D)
        tmpP3D = np.array([tmpP2D[0], tmpP2D[1], originMarker3D_Z])
        p3D = self.getRigidTargetPoint(M2D3DRigidMatrixs[0], M2D3DRigidMatrixs[1], tmpP3D)
        return p3D
    def threeD2twoD(self, p3D, M3D2DRigidMatrixs, M3D2DPerspectiveMatrixs):
        """将 3D 点投影到 2D（先刚体变换，再做二维映射）。"""
        tmpP3D = self.getRigidTargetPoint(M3D2DRigidMatrixs[0], M3D2DRigidMatrixs[1], p3D)
        tmpP2D = tmpP3D[0:2]
        p2D = self.getPerspectiveTargetPoint(M3D2DPerspectiveMatrixs, tmpP2D)
        return p2D
    def threeD2twoDFor3DSpace(self, p3D: np.array, spaceVec: np.array, 
                              plane_p1: np.array, plane_p2: np.array, plane_p3: np.array, 
                              M3D2DRigidMatrixs, M3D2DPerspectiveMatrixs):
        """沿给定方向与平面求交后，再把交点投影到 2D。"""
        p3DLine_p1 = p3D
        p3DLine_p2 = p3D + spaceVec

        intersectionP3D = self.line2plane_intersection(p3DLine_p1, p3DLine_p2, plane_p1, plane_p2, plane_p3)
        intersectionP2D = self.threeD2twoD(intersectionP3D, M3D2DRigidMatrixs, M3D2DPerspectiveMatrixs)
        return intersectionP2D
    def line2line_intersection(self, line1_p1: np.array, line1_p2: np.array, line2_p1: np.array, line2_p2: np.array):
        """计算二维直线交点；平行或重合时返回 None。"""
        # 计算直线1斜率与截距（单独处理竖直线）
        if line1_p2[0] == line1_p1[0]:
            slope1 = np.inf
            intercept1 = line1_p1[0]
        else:
            slope1 = (line1_p2[1] - line1_p1[1]) / (line1_p2[0] - line1_p1[0])
            intercept1 = line1_p1[1] - slope1 * line1_p1[0]
        
        # 计算直线2斜率与截距（单独处理竖直线）
        if line2_p2[0] == line2_p1[0]:
            slope2 = np.inf
            intercept2 = line2_p1[0]
        else:
            slope2 = (line2_p2[1] - line2_p1[1]) / (line2_p2[0] - line2_p1[0])
            intercept2 = line2_p1[1] - slope2 * line2_p1[0]
        
        # 判断是否平行（含双竖线）
        if (np.isinf(slope1) and np.isinf(slope2)) or np.isclose(slope1, slope2):
            return None
        
        # 根据斜率截距公式解交点坐标
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
        """计算两条 3D 直线的交点近似（非平行场景）。"""
        # 直线1方向向量与基点
        line1_direction = line1_p2 - line1_p1
        line1_point = line1_p1

        # 直线2方向向量与基点
        line2_direction = line2_p2 - line2_p1
        line2_point = line2_p1

        # 叉乘用于判断平行性
        cross_product = np.cross(line1_direction, line2_direction)

        # 平行或共线时没有唯一交点
        if np.allclose(cross_product, [0, 0, 0]):
            return None

        # 求解参数 t，代回 line1 获得交点
        t = np.dot(np.cross(line2_point - line1_point, line2_direction), cross_product) / np.linalg.norm(cross_product) ** 2
        intersection_point = line1_point + line1_direction * t

        return intersection_point
    def line2line_closest_midpoint3D(self, line1_p1: np.array, line1_p2: np.array, line2_p1: np.array, line2_p2: np.array):
        """计算两条 3D 直线最近点连线的中点及间距。"""
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
        """判断点 p 是否位于线段 line_p1-line_p2 内。"""
        # 线段方向向量
        line_dir = line_p2 - line_p1
        
        # 起点到点 p 的向量
        p_diff = p - line_p1
        
        # dot < 0：在线段起点外侧
        if np.dot(p_diff, line_dir) < 0:
            return False
        
        # dot > |line|^2：在线段终点外侧
        if np.dot(p_diff, line_dir) > np.dot(line_dir, line_dir):
            return False
        
        return True
    def line2plane_intersection(self, line_p1: np.array, line_p2 : np.array, 
                                plane_p1 : np.array, plane_p2 : np.array, plane_p3 : np.array):
        """计算直线与平面的交点；平行时返回 None。"""
        # 构造直线方向向量
        direction = line_p2 - line_p1
        
        # 构造平面法向量
        normal = np.cross(plane_p2 - plane_p1, plane_p3 - plane_p1)
        
        denom = np.dot(normal, direction)
        if np.isclose(denom, 0.0):
            return None

        # 计算参数 t 并回代直线方程
        t = np.dot(normal, plane_p1 - line_p1) / denom
        P = line_p1 + t * direction
        
        return P
    def pointNearest2Line(self, p: np.array, line_p1: np.array, line_p2: np.array):
        """计算点 p 到线段 line_p1-line_p2 的最近点。"""
        # 线段方向向量
        line_vec = line_p2 - line_p1
        
        # 起点到点 p 的向量
        p_vec = p - line_p1
        
        # 线段长度平方
        line_length_sq = np.dot(line_vec, line_vec)
        if np.isclose(line_length_sq, 0.0):
            return line_p1
        
        # 投影系数 t
        t = np.dot(p_vec, line_vec) / line_length_sq
        
        # t<0：最近点在线段起点
        if t < 0:
            nearest_point = line_p1
        
        # t>1：最近点在线段终点
        elif t > 1:
            nearest_point = line_p2
        
        # 0<=t<=1：最近点在线段内部
        else:
            nearest_point = line_p1 + t * line_vec
        
        return nearest_point
    def process(self,
                inputVolume: vtkMRMLScalarVolumeNode,
                outputVolume: vtkMRMLScalarVolumeNode,
                imageThreshold: float,
                invert: bool = False,
                showResult: bool = True) -> None:
        """执行阈值处理算法（可脱离 GUI 调用）。

        参数说明：
        1. `inputVolume`：输入体数据
        2. `outputVolume`：输出体数据
        3. `imageThreshold`：阈值
        4. `invert`：True 表示对高于阈值的体素置零；False 表示对低于阈值的体素置零
        5. `showResult`：是否在切片视图中自动显示输出结果
        """

        if not inputVolume or not outputVolume:
            raise ValueError("Input or output volume is invalid")

        import time

        startTime = time.time()
        logging.info("Processing started")

        # 调用 Slicer CLI 模块执行阈值运算
        cliParams = {
            "InputVolume": inputVolume.GetID(),
            "OutputVolume": outputVolume.GetID(),
            "ThresholdValue": imageThreshold,
            "ThresholdType": "Above" if invert else "Below",
        }
        cliNode = slicer.cli.run(slicer.modules.thresholdscalarvolume, None, cliParams, wait_for_completion=True, update_display=showResult)
        # 处理完成后移除临时 CLI 节点，避免场景污染
        slicer.mrmlScene.RemoveNode(cliNode)

        stopTime = time.time()
        logging.info(f"Processing completed in {stopTime-startTime:.2f} seconds")


# -----------------------------------------------------------------------------
# 兼容层说明：
#
# 历史上本文件 `BiplaneLogics.py` 主要作为算法辅助模块存在。
# 但当仓库目录被加入 Slicer 的 "Additional module paths" 后，
# Slicer 会尝试把每个顶层 `*.py` 当作 ScriptedLoadableModule 加载，
# 并期望文件中存在同名模块类 `class BiplaneLogics(ScriptedLoadableModule)`。
# 因此这里提供一个最小化的“隐藏模块”壳，避免模块实例化时报错。
# -----------------------------------------------------------------------------


class BiplaneLogics(ScriptedLoadableModule):
    """供 Slicer 发现机制使用的隐藏模块壳。"""

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
    """隐藏模块对应的空 Widget，占位以满足 Slicer 约定。"""

    def setup(self) -> None:
        ScriptedLoadableModuleWidget.setup(self)


class BiplaneLogicsLogic(ScriptedLoadableModuleLogic):
    """隐藏模块对应的空 Logic。"""

    pass
