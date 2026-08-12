# ISCE2 WSL 全链实测验证(ALOS Baja 同震对)

日期:2026-08-12。结论:**通过**。WSL 引擎环境 + §4.7 作业目录契约 + ISCE2
stripmapApp 真实全链(raw → SLC → 配准 → 干涉 → 滤波 → snaphu 解缠 → 地理编码)
端到端跑通,产出物理上合理的同震形变场。

## 数据集

GMTSAR 官方示例包 ALOS Baja EQ(本地已有,未新下载):

- 来源:`E:\00kaifaxiangmu\InSAR-Pro\insar-pro\data\test_datasets\gmtsar_alos_baja_eq\extracted`
- 事件:2010-04-04 El Mayor–Cucapah 地震(Mw 7.2,北 Baja California,T212/F0640)
- 主影像:`IMG/LED-HH-ALPSRP207600640`(FBS,震前,747 MB)
- 从影像:`IMG/LED-HH-ALPSRP227730640`(FBD,震后,383 MB;`RESAMPLE_FLAG=dual2single`)
- DEM:`topo/dem.grd`(GMT netCDF)→ `gdal_translate -of ISCE` 转 `dem.wgs84` +
  `fixImageXml.py -f` 改绝对路径

选它的理由:本地三套测试数据中,Iran burst 对缺 ASF burst XML(ISCE2 读不了),
ERS Hector 缺 PRC 轨道;只有 ALOS Baja 是自包含的原始处理级数据,且正好落在
registry `isce2_stripmap_xcorr`(条带链)的适用域。

## 执行摘要

工作区 `/home/insar/work/baja`(WSL ext4,遵守 §4.8 9p 红线);作业按 §4.7 契约
经 `wsl_wrapper.sh` 启动,`nice -n 10`、`OMP_NUM_THREADS=8`(重型计算管控)。

| 阶段 | 步骤区间 | 结果 | 耗时 |
|---|---|---|---|
| 1 | `startup → filter` | rc=0 | ~20 min(含两景聚焦) |
| 2(失败) | `unwrap → geocode` | rc=1 | 秒挂:pickle 链断裂,见教训 2 |
| 2(重试) | `filter_low_band → geocode` | rc=0 | ~13 min(snaphu ~10 min) |

产物(`interferogram/`):`filt_topophase.flat`、`phsig.cor`、`filt_topophase.unw`
(+`.conncomp`)及全部 `.geo` 地理编码版本。解缠 LOS 范围约 -4.8 ~ +7.1 m
(L 波段近场同震 + 低相干边缘解缠残差,量级合理)。断层带条纹瓣、谷地失相干、
山区高相干均符合该事件已发表结果的形态。速览图:`E:\wsl\baja_out\*.png`。
工作区占用 32 GB,验证完可整目录删除。

## 实测暴露的问题与已落地修复

1. **PROJ 数据路径缺省解析失败**。不做 `conda activate` 时 `PROJ_DATA` 未设,
   `gdal_translate`/geocode 报 `proj_create_from_database: Open of .../share/proj
   failed`。修复:`PROJ_DATA/PROJ_LIB/GDAL_DATA` 三变量固化进
   `/etc/profile.d/insar.sh`(现网已补,`scripts/wsl_setup.sh` 模板已同步)。
2. **ISCE2 `--steps` 续跑要求 pickle 链连续**。从 `filter` 停、从 `unwrap` 起,
   Application 只找"起始步骤直接前驱"(`filter_high_band`)的 pickle;它是
   分频谱占位步、从未跑过,恢复得到空状态,在 `referenceSlcCropProduct` 处以
   `NoneType` 崩溃。正确做法:从上次终点的下一步(`filter_low_band`)续起,
   空转占位步会补齐 pickle 链。`engines/isce2.py` 的 `_STEP_RANGES` 曾有同型
   缺陷(cap4 从 `mergebursts` 起跳,跳过 `ion`/`burstifg`),已改为
   `("ion", "filter")` 并在源码处写明约束。
3. **发行版用户名 ≠ 发行版名**。distro 名为 `insar`,实际用户是 `ubuntu`
   (uid 1000);`chown insar:insar` 会报 invalid user。作业统一以 root 跑
   (专用计算 VM),或用 `ubuntu` 用户。

## 复跑指引

```bash
# 数据装配(幂等):E:\wsl\baja_prep.sh + baja_setup2.sh
wsl -d insar -u root -- bash -c "tr -d '\r' < /mnt/e/wsl/baja_prep.sh | bash"
wsl -d insar -u root -- bash -c "tr -d '\r' < /mnt/e/wsl/baja_setup2.sh | bash"
# 启动(cmd.sh 内容决定步骤区间;宿主侧 wsl.exe 挂后台兼作 VM 保活)
wsl -d insar -u root -- bash /home/insar/work/baja/.job/wrapper.sh /home/insar/work/baja/.job
# 轮询
wsl -d insar -u root -- bash -c "tr -d '\r' < /mnt/e/wsl/baja_poll.sh | bash"
```
