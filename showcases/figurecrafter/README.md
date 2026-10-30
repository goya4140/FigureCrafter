# 实际案例与条件

[下载后打开离线总画廊](index.html) · [52张Nano零edit初稿](image52/index.html) · [逐文件来源与状态](cases.json)

这里展示已存在的真实产物，没有为发布仓库重新生成或修改图中的科学内容。展示目录不是生产输入。

## RoboScape

| 文件 | 方法与条件 | 状态 |
| --- | --- | --- |
| [Native PNG](roboscape/native.png) / [PPTX](roboscape/native.pptx) / [PDF](roboscape/native.pdf) | 原版v2.5.1，GPT-6.1-sol，历史缺图PDF与三张参考 | 作者模型选择；用户在对话中确认效果 |
| [Image PNG](roboscape/image.png) | 数据集原始Method，GPT-6.1-sol + Codex内置image，初稿+3次edit | 科学复审仍需修改，不作为通过样例 |
| [Hybrid图像方案](roboscape/hybrid-source.png) | 同一Method，独立于Image分支生成的内置image方案 | 中间图，携带待原生修复的问题 |
| [Decomposer PNG](roboscape/decomposer.png) / [PPTX](roboscape/decomposer.pptx) | rc1h1-method2.2，当前图服务器SAM/OCR及实际检查 | 重建完成，但像素相似度0.937014未达0.94阈值 |
| [Hybrid最终PNG](roboscape/hybrid.png) / [PPTX](roboscape/hybrid.pptx) / [PDF](roboscape/hybrid.pdf) | 导入上述重建PPTX，原版原生修订闭环 | 作者模型复审通过；human acceptance pending |

### Hybrid实际保留与限制

- 保留27个原子图片的原始字节，无新增或重编码图片；导入对象身份保留363个。
- 图片内部仍是栅格；照片中的轨迹图案不宣称可编辑。
- 数学由原生文字和线条组合，不宣称OfficeMath语义公式。
- 生成的场景和深度是示意，不能解释成模型实测结果。
- 已通过实际LibreOffice渲染观察；未进行PowerPoint/WPS现场交互验证。

Native的历史PDF输入与另外两条Method输入不同；Image与Hybrid的视觉方案又彼此独立，因此这组图只说明流程与输出形式，不能用来声称方法优劣。当前配对Hybrid将复用同一张Nano初稿，它与此历史案例分开记录。

## Nano Banana Pro：52张零edit初稿

[全部52张离线预览](image52/index.html) · [清单](image52/manifest.json)

输入为数据集原始Method及三张有序视觉参考。FigureCrafter用GPT-6.1-sol完成理解与设计，再将提示词、三张参考及4K/16:9请求交给Nano Banana Pro。实际尺寸在清单中逐张记录。

当前52/60张已生成：原44张自审为`needs_edit`；补跑8张未做科学/视觉审查。另8篇提示词超过25,000字符，按要求跳过。这里展示全部52张，不是只挑优秀结果；没有image edit。

预览JPEG经过等比例缩小以便分享。清单保留每张原始PNG的SHA256。未将预览压缩后的文件作为原始实验图或新的质量证明。

## 可从案例得出什么

可以检查三个输出路径各自长什么样、Hybrid重建损失和后续修订如何发生，以及哪些内容仍为栅格。单例和未独立审查的初稿不足以证明全面视觉提升或科学正确性。正式结论需要配对输入、版本/预算一致性、独立质量评价和实际编辑任务。
