# Recipes

Each recipe assumes a 512x512, 30 fps, 2 s composition already made with comp.new. Scale the numbers to the real canvas and duration. In the calls below, L stands for the layer's name.

## Slide across the canvas
add_keyframe {"layer":L,"path":"transform/position","keys":[{"time":0,"value":[80,256]},{"time":1.8,"value":[432,256]}],"interpolation":"easyEase"}

## Bounce twice and settle
Use the floor y for the ground (y=420) and a peak about 300 px higher.
add_keyframe {"layer":L,"path":"transform/position","keys":[{"time":0,"value":[256,100]},{"time":0.5,"value":[256,420]},{"time":0.85,"value":[256,220]},{"time":1.2,"value":[256,420]},{"time":1.45,"value":[256,340]},{"time":1.7,"value":[256,420]}]}

## Fade in
add_keyframe {"layer":L,"path":"transform/opacity","keys":[{"time":0,"value":0},{"time":0.8,"value":100}],"interpolation":"easyEaseOut"}

## Fade out
Use opacity 100 at 1.0 and 0 at 1.9. Some frames stay visible, which is fine.

## Grow or pop in
add_keyframe {"layer":L,"path":"transform/scale","keys":[{"time":0,"value":[0,0]},{"time":0.6,"value":[120,120]},{"time":0.8,"value":[100,100]}]}

## Pulse
Scale [100,100] at 0, [130,130] at 0.5, [100,100] at 1.0, [130,130] at 1.5, [100,100] at 1.9.

## Spin
add_keyframe {"layer":L,"path":"transform/rotation","keys":[{"time":0,"value":0},{"time":1.9,"value":360}],"interpolation":"linear"}

## Change color
add_keyframe {"layer":L,"path":"contents/group/contents/fill/color","keys":[{"time":0,"value":"#ff0000"},{"time":1.8,"value":"#0000ff"}]}

## Blink
Opacity 100 at 0, 0 at 0.5, 100 at 1.0, 0 at 1.5, with "interpolation":"hold".

## Title text slides up
1. execute_command layer.newText {"name":"Title","text":"HELLO","size":72,"fill":"#ffffff","position":[256,300],"justify":"center"}
2. add_keyframe on transform/position from [256,320] to [256,256], plus transform/opacity from 0 to 100, both from time 0 to 0.8.

## Two objects
Create both layers with distinct names, then add one add_keyframe call per layer. Later layers draw on top of earlier ones.

## Background color
Prefer comp.new {"background":"#101820"}. If you need a solid, create layer.newSolid first, before the artwork.

## Line or custom outline
execute_command shape.newPath {"name":"Line","vertices":[[100,256],[412,256]],"closed":false,"fill":false,"stroke":"#ffffff","strokeWidth":8}
Then animate its transform.

## Combine motions
Call add_keyframe once per property, for example position and rotation, to roll a ball while it moves.
