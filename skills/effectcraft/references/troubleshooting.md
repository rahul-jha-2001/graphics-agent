# Troubleshooting

| Error or symptom | Fix |
|---|---|
| `invalid parameters ... unknown parameter(s) X; accepted: ...` | Use only the accepted keys. For example, layer.newShape takes fill, not color. |
| `fill: a colour [r, g, b] or #hex` | Use a 6-digit hex such as "#ff0000", not "#f00" or a color name. |
| `Use an allowed command ID in command` | Use one of the nine IDs in the system prompt, under the key "command", not "id". |
| `Unknown policy tool. Use catalog.` | The name must be an action such as execute_command or add_keyframe. A command ID like "comp.new" goes inside execute_command. |
| `Expected a JSON object` / `must be str` | The `arguments` field must be a string holding one JSON object, e.g. "{}". |
| `Repeated failing action blocked` | You sent the same failing call twice. Change the arguments. |
| `Use explicit property values or keyframes` | Expressions are disabled. Write keyframes instead. |
| `no composition is open` | Run comp.new first. |
| A layer reference that matches nothing | Use the exact name you set, the numeric id from the result, or "#1" for the top layer. Check with get_comp {}. |
| A path that is not found | Call get_layer {"layer": L} and copy a path from `property_paths`. |
| `width/height/duration outside rollout bounds` | Keep the canvas at most 4096 px and the duration at most 120 s. |

## Failures the grader reports after you stop
- **constraint failed**: comp.new used different width, height, frameRate or duration than the user's constraints. Map fps to frameRate.
- **no visible graphic layer**: the artwork is missing, hidden or at opacity 0. Shapes need a kind and a fill or stroke. Text needs non-empty text.
- **no changing keyframed graphic**: add at least 2 keys with different times and values on a visible layer.
- **Rendered samples contain no visible change**: the motion is not visible. The object may be off-canvas the whole time, covered by a solid created later, too small, or keyed only after the end. Keep it on canvas, create backgrounds first, and key inside 0 to duration.

## Checking your work before stopping
get_comp {} returns the width, height, frameRate, duration and the layers from top to bottom. A background solid must be the bottom layer. Use get_property {"layer": L, "path": "transform/position"} to confirm the keys.
