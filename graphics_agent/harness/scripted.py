import json


def create_square(run):
    def action(name, **fields):
        response = json.loads(run.action(name, json.dumps(fields)))
        if not response["ok"]:
            raise ValueError(response["error"])
        return response["result"]

    action("execute_command", command="comp.new", params={"name": "Motion", "width": 512,
           "height": 512, "frameRate": 30, "duration": 2})
    layer = action("execute_command", command="layer.newShape", params={"name": "Square", "kind": "rect",
                   "size": [80, 80], "fill": "#ff0000"})["layer"]
    props = action("get_layer", layer=layer)
    if "transform/position" not in json.dumps(props):
        raise ValueError("Expected position property is missing")
    action("add_keyframe", layer=layer, path="transform/position",
           keys=[{"time": 0, "value": [80, 256]}, {"time": 1.8, "value": [432, 256]}])
