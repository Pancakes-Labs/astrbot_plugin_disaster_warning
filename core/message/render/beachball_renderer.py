"""FSSN CMT (矩心矩张量解) 震源球 (Beachball) 渲染器。

职责：
- 纯 Python + Pillow 双力偶下半球等面积投影绘制。
- 接收 strike/dip/rake 参数渲染透明 PNG 格式的沙滩球。
- 外圈描边与节面分界线共用同一像素线宽，保证视觉一致。
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from astrbot.api import logger


class BeachballRenderer:
    """双力偶沙滩球 Pillow 渲染器。"""

    def __init__(
        self,
        size: int = 512,
        line_width: int = 6,
        background_color: tuple[int, int, int, int] = (0, 0, 0, 0),
    ):
        self.size = max(1, int(size))
        self.line_width = max(1, min(20, int(line_width)))
        self.radius = self.size // 2
        self.center = (self.radius, self.radius)
        self.bg_color = background_color
        # 默认压象限填充色为纯红，张象限为纯白
        self.compress_color = (255, 0, 0, 255)  # 红色表示压象限 (P)
        self.tension_color = (255, 255, 255, 255)  # 纯白色表示张象限 (T)
        self.line_color = (0, 0, 0, 255)  # 纯黑边缘线

    def _strike_dip_slip_to_fault_plane(
        self, strike: float, dip: float, rake: float
    ) -> tuple[list[float], list[float]]:
        """将走向、倾角、滑动角转换为断层面上滑动向量及法向量。"""
        s_rad = math.radians(strike)
        d_rad = math.radians(dip)
        r_rad = math.radians(rake)

        # Aki-Richards (1980) 约定：走向 s 沿顺时针为北偏东，倾角 d 向走向右侧倾斜
        # 法向量 n (北, 东, 下)
        n = [
            -math.sin(d_rad) * math.sin(s_rad),
            math.sin(d_rad) * math.cos(s_rad),
            -math.cos(d_rad),
        ]

        # 滑动方向向量 u (北, 东, 下)
        u = [
            math.cos(r_rad) * math.cos(s_rad)
            + math.sin(r_rad) * math.cos(d_rad) * math.sin(s_rad),
            math.cos(r_rad) * math.sin(s_rad)
            - math.sin(r_rad) * math.cos(d_rad) * math.cos(s_rad),
            -math.sin(r_rad) * math.sin(d_rad),
        ]

        return n, u

    def _compute_t_p_axes(
        self, strike: float, dip: float, rake: float
    ) -> tuple[list[float], list[float]]:
        """计算 T 轴与 P 轴向量。"""
        n, u = self._strike_dip_slip_to_fault_plane(strike, dip, rake)

        t = [u[i] + n[i] for i in range(3)]
        p = [u[i] - n[i] for i in range(3)]

        norm_t = math.sqrt(sum(x * x for x in t))
        norm_p = math.sqrt(sum(x * x for x in p))

        t = [x / norm_t for x in t] if norm_t > 0 else [0.0, 0.0, 1.0]
        p = [x / norm_p for x in p] if norm_p > 0 else [0.0, 0.0, 1.0]

        # 确保轴朝向下半球 (Z > 0)
        if t[2] < 0:
            t = [-x for x in t]
        if p[2] < 0:
            p = [-x for x in p]

        return t, p

    @staticmethod
    def _normalize(v: list[float]) -> list[float]:
        norm = math.sqrt(sum(x * x for x in v))
        if norm <= 0:
            return [0.0, 0.0, 1.0]
        return [x / norm for x in v]

    @staticmethod
    def _cross(a: list[float], b: list[float]) -> list[float]:
        return [
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ]

    @staticmethod
    def _project_pixel_to_sphere(dx: float, dy: float, dist_sq: float) -> list[float]:
        """将等面积投影平面坐标逆映射为单位球面向量。"""
        z = 1.0 - dist_sq / 2.0
        z = max(0.0, min(1.0, z))

        factor_sqrt2 = math.sqrt(2.0 * (1.0 - z)) if z < 1.0 else 1.0
        if factor_sqrt2 > 0:
            proj_y = dx / factor_sqrt2
            proj_x = -dy / factor_sqrt2
        else:
            proj_x, proj_y = 0.0, 0.0

        norm_xy = math.sqrt(proj_x**2 + proj_y**2)
        target_norm = math.sqrt(1.0 - z**2)
        if norm_xy > 0:
            x = proj_x * (target_norm / norm_xy)
            y = proj_y * (target_norm / norm_xy)
        else:
            x, y = 0.0, 0.0

        norm = math.sqrt(x * x + y * y + z * z)
        if norm > 0:
            return [x / norm, y / norm, z / norm]
        return [0.0, 0.0, 1.0]

    @staticmethod
    def _sphere_to_projection(v: list[float]) -> tuple[float, float] | None:
        """将下半球单位向量映射到等面积投影单位圆坐标 (dx, dy)。

        与 `_project_pixel_to_sphere` 互为逆变换：
        dx = east * sqrt(2 / (1 + down))
        dy = -north * sqrt(2 / (1 + down))
        """
        vx, vy, vz = v
        if vz < -1e-9:
            return None
        vz = max(vz, 0.0)
        scale = math.sqrt(2.0 / (1.0 + vz))
        dx = vy * scale
        dy = -vx * scale
        return dx, dy

    def _sample_nodal_segments(
        self,
        normal: list[float],
        *,
        cx: float,
        cy: float,
        r_fill: float,
        n_samples: int = 720,
    ) -> list[list[tuple[float, float]]]:
        """采样下半球节面大圆弧，投影为屏幕折线分段。"""
        n = self._normalize(normal)
        # 构造节面内正交基
        if abs(n[2]) < 0.9:
            ref = [0.0, 0.0, 1.0]
        else:
            ref = [1.0, 0.0, 0.0]
        e1 = self._normalize(self._cross(n, ref))
        e2 = self._normalize(self._cross(n, e1))

        segments: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []

        # 多采一点闭合，便于把跨越采样边界的弧接起来
        for i in range(n_samples + 1):
            ang = 2.0 * math.pi * i / n_samples
            cos_a = math.cos(ang)
            sin_a = math.sin(ang)
            v = [
                cos_a * e1[0] + sin_a * e2[0],
                cos_a * e1[1] + sin_a * e2[1],
                cos_a * e1[2] + sin_a * e2[2],
            ]
            # 仅保留下半球（含赤道）
            if v[2] < -1e-9:
                if len(current) >= 2:
                    segments.append(current)
                current = []
                continue

            if v[2] < 0.0:
                v = self._normalize([v[0], v[1], 0.0])

            proj = self._sphere_to_projection(v)
            if proj is None:
                if len(current) >= 2:
                    segments.append(current)
                current = []
                continue

            dx, dy = proj
            # 轻微内收，避免节面线盖住外圈描边内侧
            dist = math.hypot(dx, dy)
            if dist > 1.0:
                dx /= dist
                dy /= dist
            x = cx + dx * r_fill
            y = cy + dy * r_fill
            current.append((x, y))

        if len(current) >= 2:
            segments.append(current)

        return segments

    def render(
        self,
        strike: float,
        dip: float,
        rake: float,
        output_path: str,
        line_width: int | None = None,
    ) -> str | None:
        """根据断层节面参数生成沙滩球图片。

        Args:
            strike: 走向（度）
            dip: 倾角（度）
            rake: 滑动角（度）
            output_path: 输出 PNG 路径
            line_width: 可选覆盖线宽（像素，目标分辨率下）；默认使用构造参数
        """
        try:
            lw = (
                self.line_width
                if line_width is None
                else max(1, min(20, int(line_width)))
            )

            # 1. 创建超采样图像以消除锯齿 (SSAA x2)
            scale = 2
            canvas_size = self.size * scale
            # 外圈与节面线共用同一超采样线宽
            stroke_w = max(1, int(round(lw * scale)))
            # Pillow 描边以路径为中心向两侧扩展，预留半线宽 + 1px 防裁切
            margin = (stroke_w + 1) // 2 + 1

            cx = (canvas_size - 1) / 2.0
            cy = (canvas_size - 1) / 2.0
            # 外圈描边路径半径（描边中心线）
            r_path = min(cx, cy) - margin
            if r_path <= 1.0:
                r_path = max(1.0, min(cx, cy) * 0.9)
                stroke_w = max(1, min(stroke_w, int(r_path)))
                margin = (stroke_w + 1) // 2 + 1
                r_path = min(cx, cy) - margin

            # 填充与节面线仅绘制到描边内缘，避免颜色溢出外圈
            r_fill = max(1.0, r_path - stroke_w / 2.0)
            r_outer = r_path + stroke_w / 2.0

            img = Image.new("RGBA", (canvas_size, canvas_size), self.bg_color)
            draw = ImageDraw.Draw(img)

            # 2. 计算法平面 / 滑动向量
            n_vector, u_vector = self._strike_dip_slip_to_fault_plane(strike, dip, rake)

            # 3. 像素级投影判定与着色（仅填充，不在此阶段画线）
            for y_pixel in range(canvas_size):
                for x_pixel in range(canvas_size):
                    # 使用像素中心，减少边缘锯齿与溢出
                    dx_px = (x_pixel + 0.5) - cx
                    dy_px = (y_pixel + 0.5) - cy
                    dist_px = math.hypot(dx_px, dy_px)
                    if dist_px > r_fill:
                        continue

                    dx = dx_px / r_fill
                    dy = dy_px / r_fill
                    dist_sq = dx * dx + dy * dy
                    if dist_sq > 1.0:
                        continue

                    v = self._project_pixel_to_sphere(dx, dy, dist_sq)

                    # 当 (v · n) * (v · u) > 0 时，为受压区 (Compressional)，着红色
                    # 当 (v · n) * (v · u) < 0 时，为受张区 (Tensional)，着白色
                    dot_n = sum(v[i] * n_vector[i] for i in range(3))
                    dot_u = sum(v[i] * u_vector[i] for i in range(3))

                    if dot_n * dot_u > 0:
                        draw.point((x_pixel, y_pixel), fill=self.compress_color)
                    else:
                        draw.point((x_pixel, y_pixel), fill=self.tension_color)

            # 4. 节面分界线：与外圈同一 stroke_w 的折线描边
            for normal in (n_vector, u_vector):
                segments = self._sample_nodal_segments(
                    normal,
                    cx=cx,
                    cy=cy,
                    r_fill=r_fill,
                )
                for segment in segments:
                    if len(segment) < 2:
                        continue
                    draw.line(
                        segment,
                        fill=self.line_color,
                        width=stroke_w,
                        joint="curve",
                    )

            # 5. 绘制外圆黑线（与节面线同宽，且完整落在画布内）
            draw.ellipse(
                [cx - r_path, cy - r_path, cx + r_path, cy + r_path],
                outline=self.line_color,
                width=stroke_w,
            )

            # 6. 圆形硬裁剪：外圈外侧保持透明，消除填充/抗锯齿溢出
            mask = Image.new("L", (canvas_size, canvas_size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse(
                [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer],
                fill=255,
            )
            alpha = img.split()[3]
            img.putalpha(Image.composite(alpha, Image.new("L", img.size, 0), mask))

            # 7. 缩小回目标尺寸 (SSAA 抗锯齿滤波)
            img_resized = img.resize((self.size, self.size), Image.Resampling.LANCZOS)
            img_resized.save(output_path, "PNG")
            return output_path

        except Exception as e:
            logger.error(f"[灾害预警] 沙滩球图片绘制渲染失败: {e}")
            return None
