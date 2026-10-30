# 代码、版本和运行边界

## 当前代码

| 目录 | 角色与来源 |
| --- | --- |
| `methods/drawing-v2.5.1` | 原版19文件冻结快照，支持历史PDF输入；全部保留原字节 |
| `methods/method-text/drawing-v2.5.1` | 当前Method输入适配，沿用完整模型工具循环与原四指南 |
| `methods/method-text/figurecrafter-v251-image` | Nano Banana Pro零edit实验完整后端，包含请求、实际结果登记、审查、诚实导出和重复提交保护 |
| `methods/method-text/figurecrafter-v251-hybrid` | Method输入、Decomposer不可变PPTX导入、对象差异与原版接续闭环 |
| `methods/builtin-image` | 历史Method输入的内置image扩展，累计最多3次edit；与当前零edit配置隔离 |
| `methods/decomposer/vendor/source` | 固定rc1h1-method2.2源码：识别、表示、组合、Refinement、后端、提示词和测试 |
| `methods/decomposer/vendor/knowledge-seed` | 对应知识种子，生产记录明确这一初始化来源 |
| `methods/decomposer/vendor/runtime-fonts` | 该版本附带字体及各自许可证，不替代实际字体审查 |
| `methods/decomposer/server` | 经原运行哈希核对的SAM3 / PP-OCRv5服务器worker；模型权重及第三方框架另行安装 |
| `figurecrafter.py` | 可移植派发、真实检测器调用与重建运行时适配；不添加科学设计控制器 |

Decomposer的Composition以确定性布局、编译及文字校准为主；阶段职责不等于每个阶段必须单独调用大模型。SAM/OCR只提供证据。当前服务器SAM worker返回框，不能宣称返回了像素掩膜。

原生作者默认使用公开的python-pptx等依赖；Decomposer编译使用锁定的pptxgenjs。Python虚拟环境、node_modules、LibreOffice程序、GPU依赖、模型权重和密钥不进入此分享快照。`package-lock.json`随源码提供，Node依赖通过`npm ci`安装。

## 什么没有被改动

原Native v2.5.1、四指南、既有`framework/`和`versions/`历史内容保持原样。新增Method适配只处理输入文字与必要观察门禁；后端扩展只定义图像输出或PPTX交接。没有减少标签、公式或图片，没有强制统一模板，也没有将完整模型循环替换为几个简化JSON设计阶段。

私有目录和系统路径在分享入口中改成显式本机配置。Nano传输适配只增加环境变量和本地私有配置定位；提供商、模型、请求体、初稿次数及零edit限制不变。Decomposer交接脚本只将注册表路径改为相对于仓库解析，vendor源码与提示词保持原字节。

## 来源核验

- [代码逐文件来源](../provenance/figurecrafter-source-manifest.json)：原始哈希、分享文件哈希及必要包装适配。
- [Decomposer分享清单](../methods/decomposer/hashes.json)：vendor源码、知识、字体与配置。
- [RoboScape案例来源及条件](../showcases/figurecrafter/cases.json)。
- [当前Image52清单](../showcases/figurecrafter/image52/manifest.json)：每张原始PNG哈希、实际尺寸、来源及审查状态；展示JPEG是等比例预览。

历史文档可能保留旧模型选择与旧机器路径，只作为版本记录。当前使用说明以仓库根README和`docs/`为准；正式输入以操作者选定实验的合同为准。展示目录不能作为生产模型的检索库或teacher。

本仓库没有主动运行FigureEvolver或定时任务。下载、环境检查、prepare和离线测试都不会自动开始批量生产。
