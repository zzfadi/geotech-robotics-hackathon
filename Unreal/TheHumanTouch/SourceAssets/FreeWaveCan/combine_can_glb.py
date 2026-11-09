"""Builds a one-mesh copy of the Free Wave can glb: every part's node transform baked into its vertices,
the whole can shifted so its centre is the origin (the HandoverCan proxy's pivot), all parts as
primitives of a single mesh on a single node. Usage: combine_can_glb.py <in.glb> <out.glb>"""
import json
import math
import struct
import sys

COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def main(src, dst):
    data = open(src, "rb").read()
    jlen = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20:20 + jlen])
    blen = struct.unpack_from("<I", data, 20 + jlen)[0]
    bin_ = bytearray(data[28 + jlen:28 + jlen + blen])

    def read_vec3(index):
        acc = doc["accessors"][index]
        view = doc["bufferViews"][acc["bufferView"]]
        assert acc["componentType"] == 5126 and acc["type"] == "VEC3"
        stride = view.get("byteStride", 12)
        base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        return [struct.unpack_from("<3f", bin_, base + i * stride) for i in range(acc["count"])]

    def add_vec3(values, with_bounds):
        while len(bin_) % 4:
            bin_.append(0)
        offset = len(bin_)
        for v in values:
            bin_.extend(struct.pack("<3f", *v))
        doc["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": 12 * len(values), "target": 34962})
        acc = {"bufferView": len(doc["bufferViews"]) - 1, "componentType": 5126, "count": len(values), "type": "VEC3"}
        if with_bounds:
            acc["min"] = [min(v[k] for v in values) for k in range(3)]
            acc["max"] = [max(v[k] for v in values) for k in range(3)]
        doc["accessors"].append(acc)
        return len(doc["accessors"]) - 1

    # Height of the can (glTF is Y-up): centre between its lowest and highest points
    parts = [n for n in doc["nodes"] if "mesh" in n]
    lo, hi = math.inf, -math.inf
    for node in parts:
        t = node.get("translation", [0, 0, 0]); s = node.get("scale", [1, 1, 1])
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            for p in read_vec3(prim["attributes"]["POSITION"]):
                y = p[1] * s[1] + t[1]
                lo, hi = min(lo, y), max(hi, y)
    centre = (lo + hi) / 2
    primitives = []
    for node in parts:
        assert "rotation" not in node and "matrix" not in node, "rotations are not baked here"
        t = node.get("translation", [0, 0, 0]); s = node.get("scale", [1, 1, 1])
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            prim = json.loads(json.dumps(prim))
            pos = [(p[0] * s[0] + t[0], p[1] * s[1] + t[1] - centre, p[2] * s[2] + t[2])
                   for p in read_vec3(prim["attributes"]["POSITION"])]
            prim["attributes"]["POSITION"] = add_vec3(pos, True)
            if "NORMAL" in prim["attributes"] and s != [1, 1, 1]:
                normals = []
                for n in read_vec3(prim["attributes"]["NORMAL"]):
                    v = (n[0] / s[0], n[1] / s[1], n[2] / s[2])
                    length = math.sqrt(sum(c * c for c in v)) or 1.0
                    normals.append(tuple(c / length for c in v))
                prim["attributes"]["NORMAL"] = add_vec3(normals, False)
            primitives.append(prim)
    doc["meshes"] = [{"name": "Free Wave can", "primitives": primitives}]
    doc["nodes"] = [{"name": "Free Wave can", "mesh": 0}]
    doc["scenes"] = [{"name": "Scene", "nodes": [0]}]
    doc["scene"] = 0
    while len(bin_) % 4:
        bin_.append(0)
    doc["buffers"] = [{"byteLength": len(bin_)}]
    chunk = json.dumps(doc, separators=(",", ":")).encode()
    chunk += b" " * (-len(chunk) % 4)
    total = 12 + 8 + len(chunk) + 8 + len(bin_)
    with open(dst, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<I4s", len(chunk), b"JSON") + chunk)
        f.write(struct.pack("<I4s", len(bin_), b"BIN\x00") + bin_)
    print(json.dumps({"primitives": len(primitives), "centre_y_m": round(centre, 4), "height_m": round(hi - lo, 4)}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
