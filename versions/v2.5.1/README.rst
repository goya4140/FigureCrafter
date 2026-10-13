Drawing harness v2.5.1 — 独立运行版本
=======================================

本目录是独立于 Codex 的模型驱动绘图框架。无需 Codex skill、私有 artifact-tool 或 Node 作者依赖。
模型是执行核心：科学理解、参考风格、表征、布局、作者代码、像素检查、修订和最终选择由模型完成；
工具只机械读写、执行、渲染和冻结。没有固定布局编译器或自主 evolving。
仓库根目录的 v2.5 保留，v3.5 与本目录无关。

安装与单例开始
--------------

从仓库根进入本版本目录。推荐 Python 3.13；另安装官方 LibreOffice，soffice 在 PATH 中，
或设置 DRAWING_SOFFICE 为本机可执行文件绝对路径。Windows 替换为 .venv\Scripts\python.exe。
字体由操作者合法提供，记录真实 PDF 中的字体替代；不分发系统字体。

.. code-block:: sh

   cd versions/v2.5.1
   python3 -m venv .venv
   .venv/bin/python -m pip install -r framework/requirements.lock.txt
   .venv/bin/python runtime/batch.py verify
   .venv/bin/python runtime/batch.py doctor
   .venv/bin/python runtime/batch.py test

固定数据导入
------------

PDF/PNG/PPTX、评审原图、运行输出及密钥不进入 Git。
合作者另行取得并解压 production-test-v2.5.1.zip，其顶层目录为 production-test。
导入只复制哈希匹配的缺图 PDF 和三张 PNG，98 样本共 392 文件；不导入评审资料或旧生成。
100 项清单中 048/055 是输入阻塞，不能改用其他图冒充。

.. code-block:: sh

   .venv/bin/python runtime/import-inputs.py --production-package /absolute/path/to/production-test
   .venv/bin/python runtime/batch.py prepare 001 --workspace /absolute/path/to/astra-workspaces/001-attempt-1 --model 6-astra

工作目录必须是新目录。给全新 6-astra 上下文该目录 START-HERE.txt 和本版本 .venv Python 的绝对路径，
工作根目录固定到该样本。只访问本样本及公开机械依赖，不访问其他样本、目标原图、完整参考 PDF/PPTX、
旧生成或评审资料。每样本独立上下文；工具路径边界不是任意 Python 的操作系统沙箱。
模型须看到真实 PDF/PNG 像素，不能把登记 view_image 调用当作已完成视觉检查。

批量模型接入
------------

可用 Codex 通用文件/代码/图像工具接口运行 6-astra 会话，框架无需 skill。
也可用支持视觉和工具调用的 OpenAI 兼容接口（Qwen/DeepSeek/GPT 等）或 Gemini REST；
框架示例在 framework/examples。配置实际服务提供的模型 ID、endpoint、密钥环境变量名，
密钥值只放环境变量。6-astra 用户名称不会自动变成可调用的 API model ID。
先单样本验证再批量；外部 6-astra 实际测试仍由合作者完成，不把接口 fixture 当实测。

.. code-block:: sh

   .venv/bin/python runtime/batch.py batch --workspaces /absolute/path/to/astra-http-first --config /absolute/path/to/model-config.json --samples 001 --max-turns 120
   .venv/bin/python runtime/batch.py batch --workspaces /absolute/path/to/astra-http-batch --config /absolute/path/to/model-config.json --max-turns 120

每项独立进程/对话/目录；首次失败停止并保留证据，重试另建目录，不覆盖旧 run。
模型查看全图/出版预览，review 后通过 finish 显式选择，自动交付 16:9 PPTX/PDF/PNG 并冻结。
冻结后不写入 session；核验命令：

.. code-block:: sh

   .venv/bin/python runtime/batch.py verify-frozen --workspace /absolute/path/to/astra-workspaces/001-attempt-1

冻结后评审
----------

另外解压 evaluation-v2.5.1.zip，只向新的评审上下文开放。进入评审包根目录：

.. code-block:: sh

   python3 runtime/import_frozen.py 001 --session /absolute/path/to/astra-workspaces/001-attempt-1/session --out /absolute/path/to/astra-reviews/001-attempt-1

先核验冻结所有文件及同四输入，按 review-protocol/6-astra-review-prompt.txt 对照原图和三参考审图。
评审不是评论论文研究质量，不执行精修、不回写生产或改 harness。报告存 frozen-result 之外。
评审包 baseline 是历史 v2.5，CAFE 单例是本机当前模型验证，均不能标成合作者 6-astra 新结果。

文件与来源
----------

runtime/INSTALL.zh-CN.txt、runtime/COMMANDS.zh-CN.txt 提供完整说明；protocol 提供生产及批量提示词。
framework 源码与已打包 v2.5.1 逐字一致。production-guides 保留 98 样本遮蔽过的指南快照，
MODEL_LED 只适配公开 Python 入口与固定人工流程，科学策略来源及改前/后 SHA 见 guide-provenance。
旧根目录 v2.5 的私有依赖说明只适用于旧版本，不适用于本目录。
恢复的执行器精确属于历史 008 时点尚未证明；31 项合同/接口测试和 CAFE 单例验证不证明批量科学质量。
