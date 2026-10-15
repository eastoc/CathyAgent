"""为 Agent 渲染带米制坐标、可达区和语义地标的地图。"""

from __future__ import annotations

import io
import math
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..contracts import MapContext, MapObservation, NavigationPlan, Pose2D
from .map import world_to_cell


class MapViewService:
    def __init__(self, context: MapContext, *, scale: int = 5) -> None:
        self.context = context
        self.scale = scale

    def render(self, robot: Pose2D, plan: NavigationPlan | None = None) -> MapObservation:
        context = self.context
        height, width = context.shape
        rgb = np.full((height, width, 3), (232, 235, 238), dtype=np.uint8)
        robot_cell = world_to_cell(context, robot.xy)
        component = -1 if robot_cell is None else int(context.component_ids[robot_cell])
        connected = context.traversable & (context.component_ids == component)
        rgb[context.traversable] = (205, 214, 219)
        rgb[connected] = (187, 231, 194)
        inflated_only = ~context.traversable & ~context.occupied
        rgb[inflated_only] = (247, 176, 166)
        rgb[context.occupied] = (58, 64, 71)
        # 世界 +Y 向上；数组 row 随 +Y 增大，因此图像上下翻转。
        image = Image.fromarray(np.flipud(rgb), mode="RGB").resize(
            (width * self.scale, height * self.scale), Image.Resampling.NEAREST
        )
        draw = ImageDraw.Draw(image, "RGBA")
        font = ImageFont.load_default()

        def pixel(xy: tuple[float, float]) -> tuple[float, float]:
            x = (xy[0] - context.origin_xy_m[0]) / context.resolution_m * self.scale
            y = image.height - (xy[1] - context.origin_xy_m[1]) / context.resolution_m * self.scale
            return x, y

        # 每米网格线与刻度。
        x0 = math.ceil(context.bounds_min_xy_m[0])
        x1 = math.floor(context.bounds_max_xy_m[0])
        for value in range(x0, x1 + 1):
            x, _ = pixel((float(value), context.bounds_min_xy_m[1]))
            draw.line((x, 0, x, image.height), fill=(90, 100, 110, 65), width=1)
            draw.text((x + 2, image.height - 13), f"x={value}", fill=(20, 25, 30, 220), font=font)
        y0 = math.ceil(context.bounds_min_xy_m[1])
        y1 = math.floor(context.bounds_max_xy_m[1])
        for value in range(y0, y1 + 1):
            _, y = pixel((context.bounds_min_xy_m[0], float(value)))
            draw.line((0, y, image.width, y), fill=(90, 100, 110, 65), width=1)
            draw.text((2, y + 1), f"y={value}", fill=(20, 25, 30, 220), font=font)

        if plan is not None:
            points = [pixel(point) for point in plan.waypoints_xy_m]
            if len(points) > 1:
                draw.line(points, fill=(32, 104, 210, 255), width=max(2, self.scale // 2))

        for landmark in context.landmarks:
            x, y = pixel(landmark.center_xy_m)
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 205, 55, 255))
            # Pillow 默认字体跨平台稳定支持 ASCII；中文 label 保留在结构化元数据中。
            draw.text((x + 5, y - 8), landmark.name, fill=(20, 20, 20, 255), font=font)

        rx, ry = pixel(robot.xy)
        radius_px = context.inflation_radius_m / context.resolution_m * self.scale
        draw.ellipse(
            (rx - radius_px, ry - radius_px, rx + radius_px, ry + radius_px),
            outline=(20, 86, 220, 230),
            width=max(2, self.scale // 2),
        )
        arrow = radius_px * 0.85
        draw.line(
            (rx, ry, rx + arrow * math.cos(robot.yaw), ry - arrow * math.sin(robot.yaw)),
            fill=(20, 86, 220, 255),
            width=max(2, self.scale // 2),
        )
        draw.text((8, 6), "green=reachable  red=inflated  dark=obstacle", fill=(0, 0, 0, 230), font=font)

        stream = io.BytesIO()
        image.save(stream, format="PNG")
        metadata: dict[str, Any] = {
            "frame": context.frame,
            "map_version": context.map_version,
            "robot_profile_version": context.robot_profile_version,
            "resolution_m": context.resolution_m,
            "bounds_xy_m": [list(context.bounds_min_xy_m), list(context.bounds_max_xy_m)],
            "inflation_radius_m": context.inflation_radius_m,
            "robot_pose": {"xy_m": [robot.x, robot.y], "yaw_rad": robot.yaw},
            "landmarks": [landmark.to_dict() for landmark in context.landmarks],
            "legend": {
                "green": "与机器人当前位置连通的可站立地面",
                "gray": "可站立但当前不连通的地面",
                "red": "实体外的机器人膨胀禁区",
                "dark": "实体障碍，不可作为导航目标",
            },
            "coordinate_convention": "world XY 米制；yaw=0 指向 +X，正值绕 +Z 逆时针",
        }
        return MapObservation(metadata, stream.getvalue(), image.width, image.height)


__all__ = ["MapViewService"]
