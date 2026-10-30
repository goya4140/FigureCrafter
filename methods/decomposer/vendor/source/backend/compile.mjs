import fs from "node:fs/promises";
import path from "node:path";
import PptxGenJS from "pptxgenjs";

const [inputPath, outputPath, reportPath] = process.argv.slice(2);
if (!inputPath || !outputPath || !reportPath) throw new Error("usage: compile.mjs scene.json output.pptx report.json");
const scene = JSON.parse(await fs.readFile(inputPath, "utf8"));
const pptx = new PptxGenJS();
const WIDTH = scene.canvas.width;
const HEIGHT = scene.canvas.height;
// PowerPoint cannot reliably render slides wider than 56 inches. Keep large
// source canvases in source coordinates while fitting the physical slide.
const PAGE_SCALE = Math.max(1, WIDTH / 4800, HEIGHT / 4800);
const px = value => Number(value) / (96 * PAGE_SCALE);
const pt = value => Number(value) * 0.75 / PAGE_SCALE;
const color = (value, fallback = "000000") => value === "none" ? fallback : String(value || `#${fallback}`).replace(/^#/, "").slice(0, 6);
const fontFace = (value) => {
  const first = String(value || "Arial").split(",", 1)[0].trim().replace(/^["']|["']$/g, "") || "Arial";
  const aliases = {
    "sans-serif": "Arial", "sans serif": "Arial", "system-ui": "Arial",
    "ui-sans-serif": "Arial", "-apple-system": "Arial", "blinkmacsystemfont": "Arial",
    "serif": "Times New Roman", "ui-serif": "Times New Roman",
    "monospace": "Courier New", "ui-monospace": "Courier New",
  };
  return aliases[first.toLowerCase()] || first;
};
const EAST_ASIAN_TEXT = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;
const CJK_FONT_FACE = "Arial Unicode MS";
const fontFaceForText = (value, text) => EAST_ASIAN_TEXT.test(String(text || "")) ? CJK_FONT_FACE : fontFace(value);

pptx.defineLayout({ name: "DECOMPOSER", width: px(WIDTH), height: px(HEIGHT) });
pptx.layout = "DECOMPOSER";
pptx.author = "Decomposer";
pptx.subject = "Editable scientific figure decomposition";
const slide = pptx.addSlide();
slide.background = { color: color(scene.canvas.background, "FFFFFF") };

const shapeTypes = {
  rect: pptx.ShapeType.rect,
  round_rect: pptx.ShapeType.roundRect,
  ellipse: pptx.ShapeType.ellipse,
};
const elements = new Map(scene.elements.map(element => [element.id, element]));
const manifest = [];
const fallbacks = [];
const scriptFontRoutes = [];
const lineSpacingCaps = [];

function box(element) {
  const [x, y, w, h] = element.bbox;
  return { x: px(x), y: px(y), w: px(w), h: px(h), rotate: element.rotation_deg || 0, objectName: element.id };
}

function lineStyle(style) {
  return {
    color: color(style.stroke), transparency: style.stroke === "none" ? 100 : 0,
    width: Math.max(0, pt(style.stroke_width || 0)),
    dashType: style.dash_type || (style.dashed ? "dash" : "solid"),
  };
}

function addPlaceholder(element, reason) {
  const options = box(element);
  slide.addShape(pptx.ShapeType.rect, {
    ...options,
    fill: { color: "FFF2CC", transparency: 15 },
    line: { color: "C55A11", width: 1, dashType: "dash" },
  });
  fallbacks.push({ id: element.id, reason, fallback: "visible_rect" });
}

function addLine(element) {
  const points = element.points || [];
  if (points.length < 2) throw new Error("line has fewer than two points");
  for (let index = 1; index < points.length; index += 1) {
    const start = points[index - 1];
    const end = points[index];
    const dx = end[0] - start[0];
    const dy = end[1] - start[1];
    const length = Math.hypot(dx, dy);
    if (length <= 0) continue;
    slide.addShape(pptx.ShapeType.line, {
      x: px((start[0] + end[0] - length) / 2),
      y: px((start[1] + end[1]) / 2),
      w: px(length), h: 0,
      rotate: Math.atan2(dy, dx) * 180 / Math.PI,
      objectName: `${element.id}.segment${index}`,
      line: {
        ...lineStyle(element.style),
        beginArrowType: index === 1 && element.arrow_start ? "triangle" : "none",
        endArrowType: index === points.length - 1 && element.arrow_end ? "triangle" : "none",
      },
    });
  }
}

function styledTextPayload(runs, defaultStyle) {
  const payload = [];
  for (const run of runs) {
    const runStyle = run.style || defaultStyle;
    const runText = String(run.text ?? "").replace(/\r\n?/g, "\n");
    const segments = runText.split("\n");
    for (let index = 0; index < segments.length; index += 1) {
      const text = segments[index];
      const runHasEastAsianText = EAST_ASIAN_TEXT.test(text);
      payload.push({
        text,
        options: {
          fontFace: fontFaceForText(runStyle.font_family || defaultStyle.font_family, text),
          lang: runHasEastAsianText ? "zh-CN" : "en-US",
          fontSize: Math.max(1, pt(runStyle.font_size || defaultStyle.font_size || 20)),
          color: color(runStyle.color || defaultStyle.color),
          bold: !!runStyle.bold,
          italic: !!runStyle.italic,
          subscript: run.script === "subscript",
          superscript: run.script === "superscript",
          transparency: (runStyle.color || defaultStyle.color) === "none" ? 100 : 0,
          breakLine: index < segments.length - 1,
        },
      });
    }
  }
  return payload;
}

for (const id of scene.z_order) {
  const element = elements.get(id);
  if (!element) continue;
  try {
    const options = box(element);
    const style = element.style || {};
    if (element.kind === "text") {
      const hasEastAsianText = EAST_ASIAN_TEXT.test(String(element.text || ""));
      const resolvedFontFace = fontFaceForText(style.font_family, element.text);
      if (hasEastAsianText) {
        scriptFontRoutes.push({
          id: element.id, script: "cjk", requested_font: fontFace(style.font_family),
          resolved_font: resolvedFontFace,
        });
      }
      const hasRuns = Array.isArray(element.runs) && element.runs.length > 0;
      const validRuns = hasRuns
        && element.runs.map(run => String(run.source_text ?? run.text ?? "")).join("") === String(element.text || "");
      if (hasRuns && !validRuns) throw new Error("styled text runs do not preserve source text identity");
      const textPayload = validRuns ? styledTextPayload(element.runs, style) : String(element.text || " ");
      const fontSizePt = Math.max(1, pt(style.font_size || 20));
      const normalizedText = String(element.text || "").replace(/\r\n?/g, "\n");
      const trailingBreak = /\n$/.test(normalizedText);
      const lineCount = Math.max(1, normalizedText.replace(/\n+$/, "").split("\n").length);
      const requestedLineSpacing = Number.isFinite(style.line_spacing)
        ? (style.line_spacing <= 3
          ? Math.max(1, style.line_spacing * fontSizePt)
          : Math.max(1, Number(style.line_spacing) / PAGE_SCALE))
        : Math.max(1, Number(style.font_size || 20) * 0.85 / PAGE_SCALE);
      // An explicit or default baseline advance larger than the remaining
      // native text box forces LibreOffice's normAutofit to shrink multiline
      // owners nearly to invisibility. Preserve the requested spacing when it
      // fits; otherwise cap only the vertical advance, never the font size.
      // OOXML reserves an advance for EACH paragraph, including a single line;
      // the previous (height-font)/(n-1) formula could still exceed the frame.
      // bbox is the native UNROTATED frame even when the shape is rotated.
      // Its local height, not canvas orientation, bounds paragraph leading.
      const availableAdvancePt = Math.max(1, pt(element.bbox?.[3] || 0) / lineCount);
      const lineSpacingPt = Math.min(requestedLineSpacing, availableAdvancePt);
      if (lineSpacingPt < requestedLineSpacing - 0.0001) {
        lineSpacingCaps.push({
          id: element.id, source_label_id: element.source_label_id,
          reason: "multiline-owner-box-height",
          requested_pt: Number(requestedLineSpacing.toFixed(4)),
          exported_pt: Number(lineSpacingPt.toFixed(4)),
          font_size_pt: Number(fontSizePt.toFixed(4)),
          line_count: lineCount,
        });
      }
      slide.addText(textPayload, {
        ...options,
        fontFace: resolvedFontFace,
        lang: hasEastAsianText ? "zh-CN" : "en-US",
        fontSize: fontSizePt,
        ...(Number.isFinite(style.char_spacing) ? { charSpacing: style.char_spacing / PAGE_SCALE } : {}),
        color: color(style.color), bold: !!style.bold, italic: !!style.italic,
        // LibreOffice's centered text-box metric adds a renderer-dependent
        // vertical inset and expands paragraph leading.  Source bboxes already
        // describe the visible text region, so anchor at the top and preserve a
        // measured 0.85em line advance.  The rule was verified by recompiling
        // the complete eight-sample prefix (7/8 pixel and edge improvements;
        // the sole regression stayed within the global tolerance).
        align: style.align || "center", valign: "top",
        lineSpacing: lineSpacingPt,
        margin: 0,
        fit: trailingBreak ? undefined : "shrink",
        transparency: style.color === "none" ? 100 : 0,
        ...(style.shadow ? { shadow: style.shadow } : {}),
      });
      if (validRuns) {
        manifest.push({ id: element.id, kind: element.kind, source_label_id: element.source_label_id, editable: element.editable, styled_text_runs: element.runs.length });
        continue;
      }
    } else if (shapeTypes[element.kind]) {
      slide.addShape(shapeTypes[element.kind], {
        ...options, line: lineStyle(style),
        fill: { color: color(style.fill, "FFFFFF"), transparency: style.fill === "none" ? 100 : 0 },
        ...(style.shadow ? { shadow: style.shadow } : {}),
      });
    } else if (element.kind === "polygon") {
      // The rectangle is replaced with editable DrawingML custom geometry after serialization.
      slide.addShape(pptx.ShapeType.rect, {
        ...options, line: lineStyle(style),
        fill: { color: color(style.fill, "FFFFFF"), transparency: style.fill === "none" ? 100 : 0 },
      });
    } else if (element.kind === "line" || element.kind === "polyline") {
      addLine(element);
    } else if (element.kind === "image") {
      const asset = scene.assets[element.asset_id];
      if (!asset || asset.status !== "ready") throw new Error("asset is not ready");
      slide.addImage({ ...options, path: path.resolve(asset.path), altText: element.id });
    } else if (element.kind === "path") {
      slide.addShape(pptx.ShapeType.rect, {
        ...options,
        line: {
          ...lineStyle(style),
          beginArrowType: element.arrow_start ? "triangle" : "none",
          endArrowType: element.arrow_end ? "triangle" : "none",
        },
        fill: { color: color(style.fill, "FFFFFF"), transparency: style.fill === "none" ? 100 : 0 },
        ...(style.shadow ? { shadow: style.shadow } : {}),
      });
      fallbacks.push({ id: element.id, reason: "path backend pending", fallback: "editable_rect" });
    } else {
      throw new Error(`unsupported primitive ${element.kind}`);
    }
    manifest.push({ id: element.id, kind: element.kind, source_label_id: element.source_label_id, editable: element.editable });
  } catch (error) {
    addPlaceholder(element, String(error));
    manifest.push({ id: element.id, kind: "placeholder", source_label_id: element.source_label_id, editable: true });
  }
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await pptx.writeFile({ fileName: outputPath });
await fs.writeFile(reportPath, JSON.stringify({
  status: "compiled", element_count: scene.elements.length, emitted_count: manifest.length,
  fallbacks, manifest, script_font_routes: scriptFontRoutes,
  line_spacing_caps: lineSpacingCaps,
  styled_text_run_count: manifest.reduce((total, item) => total + Number(item.styled_text_runs || 0), 0),
}, null, 2) + "\n");
