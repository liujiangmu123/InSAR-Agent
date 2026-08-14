"""Markdown report generator for deformation analysis."""


def _rate_category(mean_mm_yr):
    """Classify overall deformation regime."""
    if mean_mm_yr < -30:
        return '严重沉降'
    elif mean_mm_yr < -10:
        return '中度沉降'
    elif mean_mm_yr < -3:
        return '轻微沉降'
    elif mean_mm_yr < 3:
        return '基本稳定'
    elif mean_mm_yr < 10:
        return '轻微抬升'
    else:
        return '显著抬升'


def _funnel_line(f, accel_map):
    """Format a single funnel description line."""
    fid = f.get('id', '?')
    depth = f.get('depth_mm_yr', 0)
    rx = f.get('radius_x_km', 0)
    ry = f.get('radius_y_km', 0)
    r2 = f.get('r_squared', 0)
    lon = f.get('center_lon', 0)
    lat = f.get('center_lat', 0)

    row = f'| {fid} | {depth:.1f} | {rx:.2f} x {ry:.2f} | {r2:.2f} | ({lon:.4f}, {lat:.4f}) |'

    acc = accel_map.get(fid)
    if acc:
        vel = acc.get('velocity_mm_yr', 0)
        acc_rate = acc.get('acceleration_mm_yr2', 0)
        acc_r2 = acc.get('r_squared', 0)
        acc_flag = '**是**' if acc.get('accelerated') else '否'
        row += f' {vel:.1f} | {acc_rate:+.3f} | {acc_flag} | {acc_r2:.2f} |'
    else:
        row += ' - | - | - | - |'

    return row


def generate_markdown(report, output_path):
    """Generate a human-readable markdown analysis report.

    Args:
        report: Full analysis report dict from run_analysis().
        output_path: Path to write the .md file.

    Returns:
        Path to the written file.
    """
    meta = report.get('meta', {})
    vel = report.get('velocity', {})
    cum = report.get('cumulative', {})
    strain = report.get('strain', {})
    accel = report.get('acceleration', {})
    funnels = report.get('funnels', [])
    zones = report.get('zoning', [])
    bowl_stats = report.get('bowl_stats', {})

    path = meta.get('path', '?')
    frame = meta.get('frame', '?')
    dstart = meta.get('date_start', '?')
    dend = meta.get('date_end', '?')
    clon = meta.get('center_lon', '?')
    clat = meta.get('center_lat', '?')

    lines = []
    lines.append('# InSAR 形变分析报告')
    lines.append('')
    lines.append('## 1. 数据概况')
    lines.append('')
    lines.append(f'| 项目 | 内容 |')
    lines.append(f'|------|------|')
    lines.append(f'| 轨道 | Path {path}, Frame {frame} |')
    lines.append(f'| 监测时段 | {dstart} ~ {dend} |')
    lines.append(f'| 中心坐标 | ({clat}°N, {clon}°E) |')
    lines.append(f'| 有效像素 | {vel.get("valid_pixels", "?"):,} |')
    lines.append(f'| 沉降像素占比 | {vel.get("subsiding_pct", "?"):.1f}% |')

    if cum.get('time_steps'):
        lines.append(f'| 时序帧数 | {cum["time_steps"]} |')

    lines.append('')
    lines.append('## 2. 形变速率统计')
    lines.append('')
    lines.append(f'| 统计量 | 值 (mm/yr) |')
    lines.append(f'|--------|------------|')
    lines.append(f'| 平均值 | {vel.get("mean_mm_yr", "?"):.1f} |')
    lines.append(f'| 最小值 | {vel.get("min_mm_yr", "?"):.1f} |')
    lines.append(f'| 最大值 | {vel.get("max_mm_yr", "?"):.1f} |')
    lines.append(f'| 标准差 | {vel.get("std_mm_yr", "?"):.1f} |')
    lines.append(f'| P10 | {vel.get("p10_mm_yr", "?"):.1f} |')
    lines.append(f'| P25 | {vel.get("p25_mm_yr", "?"):.1f} |')
    lines.append(f'| P50 (中位数) | {vel.get("p50_mm_yr", "?"):.1f} |')
    lines.append(f'| P75 | {vel.get("p75_mm_yr", "?"):.1f} |')
    lines.append(f'| P90 | {vel.get("p90_mm_yr", "?"):.1f} |')
    reg = _rate_category(vel.get('mean_mm_yr', 0))
    lines.append('')
    lines.append(f'> **总体评价**: 区域平均速率 {vel.get("mean_mm_yr", "?"):.1f} mm/yr，属**{reg}**类型。')
    lines.append('')
    lines.append('## 3. 形变分区')
    lines.append('')
    lines.append(f'| 分区 | 阈值范围 (mm/yr) | 面积 (km²) | 占比 | 平均速率 (mm/yr) |')
    lines.append(f'|------|------------------|------------|------|-------------------|')
    total_area = sum(z.get('area_km2', 0) for z in zones)
    for z in zones:
        name = z.get('zone', '').replace('_', ' ').title()
        lines.append(
            f'| {name} | {z.get("rate_range_mm_yr", "?")} | '
            f'{z.get("area_km2", 0):.1f} | '
            f'{z.get("pct", 0):.1f}% | '
            f'{z.get("mean_rate_mm_yr", 0):.1f} |'
        )
    lines.append(f'| **合计** | | **{total_area:.1f}** | **100%** | |')
    lines.append('')

    lines.append('## 4. 沉降漏斗检测')
    lines.append('')
    lines.append(f'检测方法: 先高斯平滑 (σ={bowl_stats.get("smooth_sigma", "?")})，')
    lines.append(f'仅在沉降像素中 (速度 < -3 mm/yr) 检测局部极小值，')
    lines.append(f'对候选区拟合 2D 椭圆高斯，R² > 0.6 视为有效漏斗。')
    lines.append(f'最小深度阈值: {bowl_stats.get("min_depth_mm_yr", "?")} mm/yr。')
    lines.append('')

    if funnels:
        accel_map = {a.get('bowl_id'): a for a in accel.get('results', [])}
        has_accel = any(accel_map.get(f['id']) for f in funnels)
        lines.append(f'### 检测到 {len(funnels)} 个沉降漏斗')
        lines.append('')
        header = '| # | 最大深度 (mm/yr) | 半径 (km) | R² | 中心坐标 |'
        if has_accel:
            header += ' 速率 (mm/yr) | 加速度 (mm/yr²) | 加速 | 拟合 R² |'
        lines.append(header)
        sep = '|---|-----------------|-----------|-----|----------|'
        if has_accel:
            sep += '--------------|------------------|------|----------|'
        lines.append(sep)
        for f in funnels:
            lines.append(_funnel_line(f, accel_map))
    else:
        lines.append(f'**未检测到显著沉降漏斗。**')
        lines.append(f'(沉降像素占比仅 {vel.get("subsiding_pct", 0):.1f}%，')
        lines.append(f'且无满足 {bowl_stats.get("min_depth_mm_yr", "?")} mm/yr 深度阈值的聚集区域。)')

    lines.append('')
    lines.append('## 5. 累计形变')
    lines.append('')
    lines.append(f'| 指标 | 值 |')
    lines.append(f'|------|-----|')
    lines.append(f'| 最大累计位移 | {cum.get("max_displacement_mm", "?"):.1f} mm |')
    lines.append(f'| 平均累计位移 | {cum.get("mean_displacement_mm", "?"):.1f} mm |')
    lines.append(f'| 容积损失 | {cum.get("total_volume_loss_km3", 0):.6f} km³ |')
    lines.append(f'| 沉降面积 | {cum.get("subsiding_area_km2", 0):.1f} km² |')
    lines.append(f'| 总有效面积 | {cum.get("total_area_km2", 0):.1f} km² |')
    lines.append('')

    lines.append('## 6. 形变梯度 / 差异变形')
    lines.append('')
    se = strain.get('error')
    if se:
        lines.append(f'> 梯度计算失败: {se}')
    else:
        lines.append(f'| 指标 | 值 |')
        lines.append(f'|------|-----|')
        lines.append(f'| 平均梯度 | {strain.get("mean_gradient", 0):.8f} m/yr/m |')
        lines.append(f'| 最大梯度 | {strain.get("max_gradient", 0):.8f} m/yr/m |')
        lines.append(f'| 高梯度区 (>2×平均) 面积 | {strain.get("high_gradient_area_km2", 0):.3f} km² |')
        lines.append(f'| 总有效面积 | {strain.get("total_area_km2", 0):.1f} km² |')
        lines.append('')
        lines.append('> **注意**: 梯度基于 LOS 速度场，反映差异形变相对强弱。')

    lines.append('')
    lines.append('## 7. 时序加速检测')
    lines.append('')
    ac = accel.get('accelerated_count', 0)
    if funnels and ac > 0:
        lines.append(f'在 {len(funnels)} 个漏斗中，{ac} 个检测到**加速形变**趋势。')
    elif funnels:
        lines.append(f'在 {len(funnels)} 个漏斗中，未检测到显著加速趋势。')
    else:
        lines.append('无沉降漏斗，跳过加速检测。')

    lines.append('')
    lines.append('## 8. 附属图表')
    lines.append('')
    charts = report.get('charts', [])
    for c in charts:
        lines.append(f'- `{c}`')

    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('*本报告由 InSAR Agent 形变分析引擎自动生成，数据来源于 MintPy SBAS 时序处理结果。*')

    md = '\n'.join(lines)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(md)

    return output_path
