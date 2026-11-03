# 安装与完整运行

这些命令连接已存在的方法代码，不替模型完成科学设计。先在单样例验证环境，再另行决定批量范围；仓库不会自动生产或进化。

## 安装

```sh
git clone https://github.com/goya4140/FigureCrafter.git
cd FigureCrafter
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python figurecrafter.py doctor
```

已使用环境为Python 3.13。Native需要公开安装的LibreOffice；设`DRAWING_SOFFICE`为真实可执行文件路径，或将`soffice`放入PATH。Windows使用`.venv\Scripts\python.exe`，并替换为本机路径。

Hybrid还需要公开安装的Node、Poppler的`pdftoppm`，以及支持图像和JSON Schema响应的兼容模型API；无需Codex CLI：

```sh
cd methods/decomposer/vendor/source
npm ci
cd ../../../..
```

保留字体文件附带许可证，实际检查导出后的字体与公式显示。服务器另外安装SAM3、PP-OCRv5及各自GPU运行环境和合法取得的模型权重。权重、GPU框架和账户凭据未随仓库提供。

`doctor`仅报告环境位置，不宣称已经测试模型可用性或科研绘图质量。

当前入口为根目录 `figurecrafter.py`。`runtime/portable.py` 和 `runtime/runtime.example.json` 是保留的历史 v2.5 环境，含私有 artifact-tool 要求，不用于当前三方法安装。

## 独立模型配置

将 `configs/model.example.json` 复制为被Git忽略的 `configs/model.local.json`，把 `endpoint` 改成已有兼容API的完整 Chat Completions 地址。`model` 使用账户实际可用的模型ID，并与 `prepare --model` 一致；不自动换模型。密钥由 `api_key_env` 指定的环境变量提供，默认 `MODEL_API_KEY`，配置文件中不写密钥。

该API必须支持图片输入、function tools，以及完整多轮上下文。配置不是模型可用性证明。HTTP循环复用原版providers和Session，图片按实际字节发送；工具函数仍执行原版的读取、作者代码、观察、审查、修订和冻结。

Decomposer另将 `configs/decomposer.example.json` 复制为 `configs/decomposer.local.json`。模板使用同一个密钥变量，另从 `MODEL_BASE_URL` 读取API基础地址（例如已有服务的 `https://HOST/v1`）；其后自动附加 `/chat/completions`。模型名和effort同时写在 `model.name` / `model.effort` 及 `model.arguments` 的 `--model` / `--reasoning-effort` 中，修改时保持一致。模板仍用GPT-6.1-sol/medium，不替换实验模型。

`MODEL_NAME` 可不设置；已设置时须与Decomposer配置相同。适配器通过原版 `external-json` 协议调用，保留原有阶段prompt、schema与图像输入；凭据仅由允许的环境变量传递，不写入重建配置。

## 输入与隔离

每篇输入目录严格包含：

```text
method.txt
style-reference-01.png
style-reference-02.png
style-reference-03.png
```

可附`input-hashes.json`作核验。使用数据集已有Method字节，不自行追加公式解读、图注或教师说明。三张图有顺序，提供视觉参考，不提供科学事实。`examples/roboscape-method/`是一个真实的输入示例；成品在`showcases/`，不要将其开放给生产上下文。

所有workspace、检测缓存、重建输出与handoff都采用新目录。保留旧尝试，不改写冻结记录。模型必须实际打开图片，框架的`view_image`记录本身不是像素观察的证明。

## Native

```sh
export DRAWING_SOFFICE=/YOUR/LibreOffice/soffice
.venv/bin/python figurecrafter.py prepare --method native \
  --inputs examples/roboscape-method --workspace /YOUR/WORK/native-case \
  --model gpt-6.1-sol
.venv/bin/python figurecrafter.py tools --method native
```

直接启动独立HTTP工具循环：

```sh
.venv/bin/python figurecrafter.py run --method native \
  --workspace /YOUR/WORK/native-case --config configs/model.local.json
```

模型读取指南与输入，写理解、表征、决策及builder，通过`execute → inspect → 实际查看像素 → review → 修订 → finish`运行完整循环。`--max-turns`默认80，是运行步数上限；到达上限保留断点，不制造交付或降低质量标准。重新执行同一命令可接续未冻结会话。

如使用手动工具桥接，将[通用启动说明](MODEL_START_PROMPT.md)交给支持文件、代码和实际图像查看的模型环境。无需Codex特有技能。

工具调用示例只说明传输，不预先指定科学内容：

```json
{"name":"read_text","arguments":{"path":"run/inputs/method.txt"}}
```

```sh
.venv/bin/python figurecrafter.py call --method native \
  --workspace /YOUR/WORK/native-case --json /YOUR/WORK/native-case/work/request-001.json
.venv/bin/python figurecrafter.py verify --method native --workspace /YOUR/WORK/native-case
```

输出为`delivery/main-figure.pptx`、PDF、PNG及审查/来源记录。原版PDF入口单独保留为`native-pdf-historical`，当前新生产使用Method入口。

## Image：当前Nano零edit配置

凭据通过环境变量或本地文件提供：

```sh
export NANO_API_KEY=YOUR_PRIVATE_KEY
export NANO_BASE_URL=https://api.80ai.net
```

也可将`configs/nano.example.json`复制为被Git忽略的`configs/nano.local.json`，在本机填写，或通过`FIGURECRAFTER_API_CONFIG`指向工作区外的私有JSON。不要将密钥写进代码、日志、提示词或共享归档。

```sh
.venv/bin/python figurecrafter.py prepare --method image \
  --inputs examples/roboscape-method --workspace /YOUR/WORK/image-case \
  --model gpt-6.1-sol
.venv/bin/python figurecrafter.py tools --method image
.venv/bin/python figurecrafter.py run --method image \
  --workspace /YOUR/WORK/image-case --config configs/model.local.json
```

模型仍需完整读取Method和三张参考，写`work/understanding.md`、`representation.md`、`decision.md`及绘图提示词文件。`image_request`的`prompt`参数是文件路径：

```json
{"name":"image_request","arguments":{"mode":"generate","prompt":"work/image-prompt.txt"}}
```

实际接口为`POST /api/tasks/submit`：`mode=generate`、`model=banana_pro_edit`、`size=16:9`、`resolution=4K`，携带完整提示词和三张参考图。模型名包含`edit`不意味着本次进行了image edit。

随后通过同一个Session调用：

```json
{"name":"provider_submit","arguments":{"request":"000"}}
```

```json
{"name":"provider_poll","arguments":{"request":"000"}}
```

`provider_poll`每次仅GET同一任务；成功后导入实际图片。不得因任务等待而重复POST。未知提交结果保留intent，先查询任务/账单，不自动重发。

模型实际查看全图与出版预览后记录`image_review`。`finish`要求接受状态；零edit初稿仍有问题时可以`export_initial`，但会将`needs_edit`如实保留。初稿次数1、edit次数0，不能静默重生成以规避预算。

运行记录曾遇服务端25,000字符限制。发现超长时保留原提示词，按实验协议跳过或另行授权处理，不能截断科学内容。接口限制不应通过修改科学方法掩盖。

## Image：通用内置image模式

`--method image-builtin`使用相同Method适配和原四指南，保留内置image工具的请求/实际结果登记/审查/冻结代码。该模式需要操作者环境真的提供内置image工具；`image_request`返回的参数必须交给实际工具，不能伪造图片或receipt。

这是历史可选分支，当前HTTP `run`入口不接受 `image-builtin`，不会静默换成Nano。仅当前 `image`（Nano零edit）路径可独立运行。Nano服务与模型也仍须由操作者提供，不能将其称为本地离线图像生成。

累计最多3次image edit，失败、分支与弃用请求同样计入；另有共享预算模块。它与Nano零edit模式是不同实验配置，不能在同一个样例中切换来绕过预算。仓库不声称当前Nano零edit传输支持3次外部API编辑。

## Hybrid：复用现有图像并检查SAM/OCR

配对实验可直接复用Image初稿及已保存规划，无需再生成Nano图。保留原始文件和哈希。交给Decomposer的画布应严格16:9；若源图略有偏差，仅补白边并记录偏移，禁止非等比拉伸、裁剪或改画内容。当前Image后端的`candidate.png`及正式导出已执行并披露这一处理。

先按`configs/detectors.example.json`配置实际SAM3和PP-OCRv5服务器命令，存为`configs/detectors.local.json`。示例worker位于`methods/decomposer/server/`，请求通过JSON stdin传递base64图片、canvas和options；响应JSON stdout，诊断走stderr。`methods/decomposer/vendor/source/decomposer/detectors.py`定义完整协议。

```sh
.venv/bin/python figurecrafter.py detectors \
  --image /YOUR/WORK/image-case/delivery/main-figure.png \
  --config configs/detectors.local.json --cache-root /YOUR/WORK/detector-cache \
  --sample-id example-001 --preflight /YOUR/WORK/preflight-001
```

模型必须在Recognition之前实际查看原图、`sam-all.png`、`sam-pictures.png`、`ocr-all.png`、候选JSON及summary，记录缺失、误检、公式转写损坏等。当前worker只返回候选框，不声称是像素掩膜。失败先处理，不无限等待，也不自动换成本机OCR。

实际检查后写`/YOUR/WORK/preflight-001/review.json`，至少包括：

```json
{
  "status":"reviewed",
  "image_sha256":"SHA256_OF_ACTUAL_16_9_IMAGE",
  "candidates_sha256":"SHA256_OF_ACTUAL_candidates.json",
  "sam_observations":["填写真实覆盖与缺失"],
  "ocr_observations":["填写真实文字与公式问题"],
  "limitations":["填写实际限制"],
  "notes":"实际像素检查记录；不是科学验收"
}
```

模板不能直接当作已检查证据。图片和候选哈希必须匹配实际文件。

## Hybrid：Decomposer重建

```sh
.venv/bin/python figurecrafter.py --soffice /YOUR/soffice reconstruct \
  --image /YOUR/WORK/image-case/delivery/main-figure.png \
  --cache-root /YOUR/WORK/detector-cache --sample-id example-001 \
  --review /YOUR/WORK/preflight-001/review.json \
  --output /YOUR/WORK/decomposer-001 --handoff /YOUR/WORK/handoff-001 \
  --model-config configs/decomposer.local.json --node /YOUR/node --pdftoppm /YOUR/pdftoppm
```

该命令复用固定Decomposer的Recognition、Representation、Composition及Refinement，不更改提示词或阈值。模板配置模型为GPT-6.1-sol/medium；服务须实际支持，不会默默回退。`{python}`和`{repo}`在运行时解析为当前解释器与仓库位置，无需原作者的Codex、缓存目录或机器路径。

入口生成的实际 `--config-overlay` 显式包含 `refinement.max_rounds=3`；有效重建配置也应显示3。冻结基础配置仍为1，不改动其字节。若明确需要历史Codex传输，可用 `--codex /YOUR/codex` 代替 `--model-config`；两者互斥，Codex不是默认依赖。

handoff包含实际`editable.pptx`、`preview.png`、`scene.json`、`source-image.png`及哈希。保真阈值未通过时会保留明确的质量状态；重建完成并不代表科学质量通过。

## Hybrid：原版FigureCrafter接续

```sh
.venv/bin/python figurecrafter.py --soffice /YOUR/soffice prepare --method hybrid \
  --inputs examples/roboscape-method --workspace /YOUR/WORK/hybrid-native-001 \
  --handoff /YOUR/WORK/handoff-001 --model gpt-6.1-sol \
  --context /YOUR/WORK/image-case/work/understanding.md \
  --context /YOUR/WORK/image-case/work/representation.md \
  --context /YOUR/WORK/image-case/work/decision.md \
  --context /YOUR/WORK/image-case/delivery/visual-review.json
```

启动单独的原生接续HTTP上下文：

```sh
.venv/bin/python figurecrafter.py --soffice /YOUR/soffice run --method hybrid \
  --workspace /YOUR/WORK/hybrid-native-001 --config configs/model.local.json
```

模型按照MODEL、CONTINUATION和原四指南运行完整循环。builder必须打开不可变导入PPTX，在对象上修改；同时输出`continuation-receipt.json`，记录实际对象修改与依据。

`execute`生成真实渲染、对象清单与`continuation-diff.json`。模型观察、比较、按需要修订，通过原版`review/finish`选择结果。保留可用的视觉表达和原子图片，科学纠错与视觉损失分别报告。不允许以整页截图、空白重画或固定小修脚本冒充接续。

```sh
.venv/bin/python figurecrafter.py verify --method hybrid --workspace /YOUR/WORK/hybrid-native-001
```

验证冻结文件不等于独立质量评估。真实PowerPoint/WPS编辑检查需另行执行并记录，不能用LibreOffice渲染冒充。
