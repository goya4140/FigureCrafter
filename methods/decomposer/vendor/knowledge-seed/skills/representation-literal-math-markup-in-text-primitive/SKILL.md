---
name: representation-literal-math-markup-in-text-primitive
description: Prevention strategy for literal-math-markup-in-text-primitive.
---

Convert mathematical markup to supported editable Unicode notation before placing it in ordinary text primitives; verify that commands and delimiters never render literally.

Represent mixed prose and mathematical identifiers with editable styled text runs whose source_text preserves Recognition identity while visible text uses native italic and subscript/superscript properties; verify exported OOXML text and baselines.

Downstreamed: integrated into the current pipeline and supported by measured proof.
