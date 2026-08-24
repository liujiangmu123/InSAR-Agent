# 偏移时间序列查看器(预留)

匹配文件名同时含后缀 `.h5` / `.nc` / `.tif`,且名字里有 `offset`、`GOFF`、`azimuthOffset`、`rangeOffset` 或 `Off`(大小写不敏感;两项 AND,故 `velocity.h5` 不会误配)。

`status=reserved`:第 52 号研究不做自研 pixel offset / MAI,本仓没有偏移追踪科学实现;NISAR GOFF 可作模式 C 输入,侧栏只显示预留。

下一步:把 `plugin.yaml` 的 `status` 改为 `ready`,在 `engines/figures.py` 增加偏移场出图,或给 `/api/timeseries-point` 做偏移立方体取样兼容层并在 overlay 接侧栏;不要在本目录放 `.py` 执行入口。
