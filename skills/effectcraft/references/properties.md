# Property paths and value formats

Pass these paths to add_keyframe, set_property and get_property. Values set at a key time hold until the next key.

## Every layer
- transform/position: [x,y] in pixels, measured from the top-left of the canvas.
- transform/scale: [sx,sy] in percent. 100 is the original size, 0 is invisible.
- transform/rotation: degrees, clockwise. 360 is one full turn.
- transform/opacity: 0 is invisible, 100 is solid.
- transform/anchor: [x,y], the pivot for rotation and scale, relative to the layer. Shapes default to [0,0], their own center.

## Shape layer with one shape (layer.newShape)
- contents/group/contents/fill/color: "#rrggbb"
- contents/group/contents/fill/opacity: 0 to 100
- contents/group/contents/ellipse/size: [w,h] (kind ellipse)
- contents/group/contents/rect/size: [w,h] and contents/group/contents/rect/roundness in px (kinds rect and rounded)
- contents/group/contents/star/outerRadius, contents/group/contents/star/innerRadius, contents/group/contents/star/points, contents/group/contents/star/rotation (kinds star and polygon; polygon has no inner radius)
- With a stroke: contents/group/contents/stroke/color and contents/group/contents/stroke/width
- contents/group/transform/position, contents/group/transform/scale, contents/group/transform/rotation: the group inside the layer. Prefer the layer's own transform/... paths.

## Several shapes in one layer
shape.newShape {"layer": NAME, "kind": "star", ...} adds a group on top of the layer's contents. The newest group is contents/group and older groups become contents/group#2, #3, and so on. After adding shapes, call get_layer to read the real paths.

## Text layer (layer.newText)
- Creation params: text, size, fill ("#rrggbb"), position [x,y], font, justify (left, center or right), tracking, name.
- text/sourceText: the string.
- Prefer transform/position, transform/scale and transform/opacity to animate text.

## Solid layer (layer.newSolid)
- Params: color, name, width, height. It defaults to the full canvas size, so it covers every layer below it.

## Paths you draw (shape.newPath)
- Params: vertices [[x,y],...], closed (true or false), fill ("#rrggbb" or false), stroke, strokeWidth, name.
- Vertices are in comp pixels unless space is "layer".

## Value types
- scalar: 50
- vec2 or vec3: [x,y]. The engine fills z with 0 for you.
- color: "#rrggbb" or [r,g,b,a] with each channel from 0 to 1.
- enum: an integer from the options list that get_property returns.

## Interpolation (add_keyframe "interpolation")
- linear: constant speed.
- easyEase: slow at both ends. Natural for most moves.
- easyEaseIn and easyEaseOut: ease into or out of the key.
- hold: jump with no in-between frames. Good for blinking.
- bezier: the default curve.
