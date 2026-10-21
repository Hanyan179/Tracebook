# 声记标志

以“声音被留下”为含义：开口的纵向容器保留一枚内侧声纹。采用同一个轮廓，界面中按主文字颜色显示，自动适配浅色和深色。

- 源标志：`../Apple/Resources/Brand.xcassets/BrandMark.imageset/brand-mark.png`
- 应用图标原图：`app-icon-original.png`
- 三端图标：`../Apple/Resources/PhoneIcons.xcassets`、`MacIcons.xcassets`、`WatchIcons.xcassets`
- 生成方式：内置 ImageGen（图像生成），没有使用用户的模型接口密钥。
- `scripts/prepare-brand-assets.py` 只进行平台要求的尺寸转换与打包，不重新绘制轮廓。

## 原始生成提示词

```text
Use case: logo-brand. Create one final standalone brand symbol for an elegant native voice-memory app named 声记 (do not draw any text). The meaning is a spoken moment kept as a memory. A single distinctive abstract mark: a compact upright rounded sound capsule with a deliberate small opening and an offset short inner stroke, suggesting both a sound pulse and a retained page/fold, sculptural but completely flat. The mark must read beautifully at 24px. Strict monochrome: solid near-black shape on fully transparent background. Sophisticated Swiss graphic restraint, balanced optical spacing, thick consistent geometry, softened precise ends, recognizable negative space. Centered square composition, symbol occupies about 62 percent of the canvas with generous clear space. No letters, no wordmark, no microphone clip-art, no generic equalizer bars, no brain, no infinity sign, no Grok/xAI logo, no gradients, no shadows, no 3D, no mockup, no border, no extra sample variants. Output a clean high-resolution transparent PNG brand mark.
```

## 应用图标变体提示词

```text
Edit target: the attached original 声记 brand symbol. Produce its final app icon. Preserve the symbol's exact outer and inner contours, proportions, opening at top and diagonal-ended inner stroke, with no redesign. Change only presentation: render the symbol as perfectly flat pure white, centered on an opaque uniform near-black #111111 square canvas. Symbol height 64% of square, generous negative space. No text, no shadows, no gradient, no texture, no additional strokes, no border, no rounded-corner mask (the operating system applies it). One full-bleed square app icon, 1024x1024.
```
