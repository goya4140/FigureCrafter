# Drawing harness v2.5：历史恢复版

恢复日期：2026-10-11。锚点为 `model-led-isolated-093-v2` 的冻结图稿迭代 `008`，不是当前 v3.5。版本号 v2.5 由用户为本次恢复指定，不代表当时已有此版本标签。

本目录独立保存指南、执行器候选、完整实验与作者辅助文件。当前 v3.5、v3、v2冻结快照、原根目录和既有实验/交付均不覆盖。未启动新的绘图或进化。

## 恢复内容与证据等级

|内容|位置|来源与确定性|
|---|---|---|
|当时四份通用指南|`harness/MODEL_LED_DRAWING.zh-CN.md`、`OBJECT_COMPOSITION.zh-CN.md`、`STYLE_LEARNING.zh-CN.md`、`REFERENCE_JOINT_LEARNING.zh-CN.md`|从该实验快照原样复制，四份均匹配当时隔离协议哈希；没有用当前指南重建|
|当时008作者代码及全实验|`evidence/experiment/`|原样保留所有迭代、输入、决策、理解、表征、审查和渲染。008 `build.mjs`、理解/表征匹配launch记录哈希；四个科学输入匹配协议哈希|
|当时交付|`evidence/delivery/`|原样复制；freeze记录中的9份文件哈希全部核验|
|当时作者辅助文件|`evidence/author-support/`|原样复制尚存的裁剪、数学下标处理代码、字体配置和作者记录；这些辅助文件没有独立历史哈希，确定性低于上述核验项|
|机械执行器及证据工具|`harness/model_led_drawing.py`、`scripts/pptx_object_evidence.py`等|从后来v2冻结快照原样提取，匹配该快照manifest。当前根执行器与该快照相同；但008没有记录其源码哈希，不能证明这就是008时点精确字节|
|完整历史源码候选档案|`archive/later-v2-frozen/`|保留后来v2冻结目录所有源码、指南、脚本与测试，包括历史非主入口和外层代码。它是候选档案，不把后来的外层v3等文件声称为008实际使用过的文件|
|恢复版隔离入口|`run.py`、`scripts/__init__.py`|本次新建的重建文件。加载本地执行器，prepare固定四份恢复指南与人工外层，不引入v3.5指南或自主进化|

逐文件来源、SHA256、证据等级及核验结果见 [recovery-manifest.json](recovery-manifest.json)。347份恢复/入口文件均逐字节核验；manifest与本说明属于本次新增的恢复记录，不是当时文件。

## 独立使用

在项目根目录执行：

```sh
.venv/bin/python framework_versions/drawing-v2.5/run.py identity
.venv/bin/python framework_versions/drawing-v2.5/run.py drawing --help
.venv/bin/python framework_versions/drawing-v2.5/run.py drawing prepare \
  --root framework_versions/drawing-v2.5/runs/NEW_RUN \
  --pdf /absolute/paper-without-main-figure.pdf \
  --reference /absolute/style-a.png --reference /absolute/style-b.png
```

后续 `execute`、`review`、`deliver` 参数见CLI及恢复的主合同。模型仍负责论文理解、参考学习、构图、作者代码和像素审查；运行CLI不自动执行模型绘图。新作品必须另建run，不修改证据目录。入口仅支持identity/drawing，没有默认外层自主进化。

`verification/prepare-smoke/`是本次输入准备测试，使用保存的1PDF+3PNG核验本地入口和四份指南加载；不是新的绘图实验。

## 不能确认或尚未恢复的部分

- Git尚无提交历史；008只快照了作者代码/决策/指南等，没有整个执行器源码快照或源码哈希。因此这是“可核验原始记录＋历史执行器候选＋新隔离入口”的恢复，不宣称完整时点源码已百分之百证明。
- 原作者代码中的绝对路径原样保留；不把它改写成假装原始的可移植代码。其依赖的presentations技能 `26.1007.11041` 路径目前缺失。外部artifact runtime、Python、字体、LibreOffice等当时没有密封，不能用当前依赖替代后声称精确复现。
- 008人脸裁剪、数学下标处理脚本和字体配置已经找回，但模型会话内部状态没有文件化；即使运行环境补齐，新生成也不保证像素相同。
- 这次没有重新生成008，也没有证明旧版质量一定优于v3.5。恢复目录保留了原交付，便于之后在同输入下比较。

## 验证范围

已核验入口identity与help、准备流程、恢复指南及输入哈希、008作者/理解/表征与交付哈希。当前v3.5的harness源码、入口与合同在本任务前后哈希未变；整个v3.5目录同期出现其他run/素材记录变化，故不声称整棵目录静止。本恢复的写入范围仅本v2.5目录。
