Drawing harness v2.5
====================

从缺方法主图的论文 PDF 和参考图片生成单页 16:9、原生可编辑的科研方法主图。
模型负责论文理解、参考学习、表征、布局、作者代码及实际像素审核；代码负责文件准备、执行、渲染与证据保存。

此仓库用于 private 协作，固定恢复的 v2.5，不含 v3.5 或自主 evolving harness。
它不是一键模型 API 客户端；6-astra 会话由合作者自己的智能体环境提供。

包含什么
--------

* ``framework/``：原样复制的恢复入口、执行器候选、证据工具、依赖锁和 23 项合同测试。
* ``production-guides/``：本轮 98 个合格样本实际使用的四指南快照；相关目标的历史描述沿用已核验遮蔽副本。
* ``runtime/``：另建的可移植准备、环境探测、字体配置、冒烟与冻结工具，安装说明及运行命令。
* ``protocol/``：6-astra 生产和外层批量任务管理提示词。
* ``manifests/``：100 项状态、392 份固定科学输入哈希、指南及框架来源、仓库文件完整性清单。
* ``provenance/``：完整原始四指南与恢复来源清单，仅供管理者核验；不能代替对应生产指南供模型读取。

PDF、图片、PPTX、旧图稿、评审材料、虚拟环境、账号密钥、系统字体和私有作者依赖均不放在 Git 中。
科学数据通过独立的 ``production-test.zip`` 提供；``evaluation.zip`` 仅供冻结后的新评审上下文。

安装与开始
----------

先读 ``runtime/INSTALL.zh-CN.txt``。在仓库根目录运行：

.. code-block:: sh

   python3 -m venv .venv
   .venv/bin/python -m pip install -r runtime/requirements.lock.txt
   cp runtime/runtime.example.json runtime/runtime.local.json

填写本机 Node、LibreOffice、合法提供的 artifact-tool 入口和 presentations 技能路径。
Windows 将 Python 命令换成 ``.venv\Scripts\python.exe``，配置本机可执行文件路径。
artifact-tool 是私有依赖，未随仓库分发；不能假设公共 npm 可安装，也不能默默改用其他绘图后端。
参考环境是 Python 3.13.5、Node 24.19.0、artifact-tool 2.8.91、presentations 技能 26.1009.11318。
依赖、字体或版本不同必须记录实验条件变化。

.. code-block:: sh

   .venv/bin/python runtime/portable.py verify
   .venv/bin/python runtime/portable.py doctor --report environment-check.json
   .venv/bin/python runtime/portable.py test
   .venv/bin/python runtime/smoke.py --out workspaces/environment-smoke-1

必要时先用 ``configure-fonts.py --font-dir`` 指定合法字体目录，实际查看 PDF 字体与全图/出版预览。
以上为机械环境检查，不等于科研绘图质量检查。

导入固定数据与批量生产
----------------------

完整解压独立生产包，然后只导入核验过的科学输入：

.. code-block:: sh

   .venv/bin/python runtime/import-inputs.py --production-package /path/to/production-test
   .venv/bin/python runtime/portable.py prepare 001 --workspace workspaces/001-attempt-1 --model 6-astra --reasoning record-at-launch

导入会核对全部 392 个输入文件，且不覆盖已有 ``production-inputs/``。
数据不在仓库中；没有生产包时，不能声称本轮固定数据已就绪。
每个样本 prepare 到全新目录，启动独立新 6-astra 上下文，把该目录作为工作根，只提供 ``START-HERE.txt``。
不要让生产模型浏览整个仓库的其他样本或 ``provenance/``，不要提前开放评审包。
每样本科学输入严格是同一份缺图 PDF 加三张 PNG；对应四指南和机械依赖另行提供。
这是上下文与访问约束隔离，不是操作系统沙箱。

先完成一个样本验证链路，再按 ``protocol/batch-controller-prompt.txt`` 批量。
每个样本新上下文和目录，可以并行；失败保留并另建重试目录，不覆盖历史记录。
100 项中 98 项可生产，048/055 是已核验的非方法主图输入阻塞，不强行绘制其他图代替。
不运行外层自主进化，不把冻结后诊断传回生产。

审核、冻结与测评
----------------

具体 ``execute → review → deliver → freeze`` 命令见 ``runtime/COMMANDS.zh-CN.txt``。
模型自己检查真实全图及出版预览、科学关系、字体、图像来源和代表对象编辑探针，明确选择迭代。
原执行器历史 ``canvas_mm/bounds_mm`` 字段有十倍单位偏差，以原始 EMU 和 PDF 尺寸为准；保留源码，不用错误字段做布局。
填好实际模型标识等实验设置，再调用 ``freeze.py``；机械冻结不代替模型质量判断。

冻结后用新评审上下文和独立评审包比较原图、参考及新生成结果，判别并过滤问题。
独立评审先完成并冻结，再打开旧基线做模型比较；测评不是论文质量评论，也不是回改本轮作品的精修步骤。

版本来源与限制
--------------

v2.5 恢复锚点是 ``model-led-isolated-093-v2/008``。四指南有当次快照 SHA 核验。
执行器取自后来 v2 冻结候选，无法证明是 008 时点精确源码；恢复入口和运行兼容层为后续重建。
完整来源清单是历史记录，本仓库只携带框架相关文件，并未上传全部 347 份历史实验证据。
本仓库新增文件仅做可移植机械适配，未修改原 v2.5 恢复文件或 v3.5。
真实 LibreOffice、字体回退和代表对象编辑检查不构成所有应用/对象的互操作保证。

独立运行的新版本 v2.5.1
----------------------

合作者使用公开依赖版本，请进入 ``versions/v2.5.1/``，阅读其中 README.rst。
该版本不需要 Codex skill 或私有绘图库，包含安装、数据导入、6-astra 隔离生产及冻结后评审说明。
本 README 之前部分仅说明保留的 v2.5；其私有依赖不适用于 v2.5.1。
