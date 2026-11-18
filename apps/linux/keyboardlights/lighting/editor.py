"""Desktop editor state; device work stays in the validated LightingService."""

import json
import logging
import os
import stat
import tempfile
from copy import deepcopy
from itertools import combinations
from pathlib import Path

import keyboardlights.lighting.service as lighting
from keyboardlights.profiles.io import MAX_DRAFT_BYTES, encode_profile


def neighboring_keys():
    """Connect shared key edges, allowing the layout's quarter-key horizontal gutters."""
    neighbors = {key["led_index"]: set() for key in lighting.KEYS}
    for key, other in combinations(lighting.KEYS, 2):
        a = key["display"]
        b = other["display"]
        horizontal = (
            0 <= b["left"] - a["left"] - a["width"] <= 1 or 0 <= a["left"] - b["left"] - b["width"] <= 1
        ) and min(a["top"] + a["height"], b["top"] + b["height"]) > max(a["top"], b["top"])
        vertical = (a["top"] + a["height"] == b["top"] or b["top"] + b["height"] == a["top"]) and min(
            a["left"] + a["width"], b["left"] + b["width"]) > max(a["left"], b["left"])
        if horizontal or vertical:
            neighbors[key["led_index"]].add(other["led_index"])
            neighbors[other["led_index"]].add(key["led_index"])

    return {index: frozenset(adjacent) for index, adjacent in neighbors.items()}


KEY_NEIGHBORS = neighboring_keys()


class LightingEditor:
    def __init__(self, service=None, capture=None, *, history_path=None):
        self.history_path = Path(history_path) if history_path is not None else None
        self.last_used_color = "#00FFFF"
        saved_color = self._read_history(self.history_path)
        if saved_color is not None:
            self.last_used_color = saved_color

        self.bucket_history_path = self.history_path.with_name("last-bucket-color.txt") if self.history_path else None
        self.last_bucket_color = self._read_history(self.bucket_history_path) or "#00FFFF"
        self.bucket_color = self.last_bucket_color
        self.paint_tool = "pointer"
        self.service = service
        self.image = bytes(capture) if capture is not None else None
        self._revision = 0
        self._change_cache = None
        if capture is not None:
            lighting.validate(capture)

        self.adopt(lighting.image_state(self.image) if service is None else service.state())

    @staticmethod
    def _read_history(path):
        if path is None:
            return None

        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    return None

                color = os.read(descriptor, 8).decode("ascii").strip().upper()
            finally:
                os.close(descriptor)

            lighting.rgb(color)
            return color if color != "#000000" else None
        except (OSError, ValueError):
            return None

    def adopt(self, state):
        previous_brush = getattr(self, "brush_color", None)
        self._revision += 1
        self.unpainted = set()
        self._static_brightness = None
        if self.service is not None:
            self.image = self.service.baseline

        self.state = state
        self.review = None
        self.selection = set()
        self.draft = None
        self.workspace_style = None
        self._keyboard_preview = None
        if not state["available"]:
            return

        selected, slots = state["selected"], state["slots"]
        colors = {slots[index]["color"] for index in lighting.PHYSICAL if slots[index]["enabled"]}
        solid = len(colors) == 1 and all(slots[index]["enabled"] for index in lighting.PHYSICAL)
        tag = selected["tag"]
        color = next(iter(colors)) if tag == 128 and solid else selected["color"]
        if 1 <= tag <= 6:
            color = lighting.LEGACY_COLORS[tag - 1]

        style = ("solid" if solid else "perKey") if tag == 128 else "animation" if 7 <= tag <= 15 else "solid"
        self.workspace_style = style
        if style == "animation":
            self.select_pointer()

        self.brush_color = color if previous_brush is None else previous_brush
        self.draft = {"style": style, "color": color,
            "brightness": min(16, max(1 if style == "animation" else 0, selected["brightness"])),
            "mode": tag if 7 <= tag <= 15 else 8, "speed": min(16, max(2, selected["speed"] or 12)),
            "generated": selected["generated"], "direction": 1 if selected["direction"] == 1 else 0,
            "slots": deepcopy(slots)}
        self._keyboard_preview = deepcopy(self.draft)

    def has_changes(self):
        """Cache effective-byte comparisons until a draft mutation or new image.

        Treat ``draft`` as read-only outside change/paint/import methods. Key
        selection and widget refreshes do not invalidate this calculation.
        """
        if self.image is None or self.draft is None or self.has_unpainted():
            return False

        revision, image, draft = self._revision, self.image, self.draft
        if self._change_cache is not None:
            cached_revision, cached_image, cached_draft, changed = self._change_cache
            if cached_revision == revision and cached_image is image and cached_draft is draft:
                return changed

        try:
            lighting.candidate(image, draft)
        except ValueError:
            changed = False
        else:
            changed = True

        # Keep object references, so a replaced draft cannot reuse a cached ID.
        self._change_cache = (revision, image, draft, changed)
        return changed

    def reset(self):
        self.adopt(self.state)

    def has_unpainted(self):
        return self.workspace_style != "animation" and bool(self.unpainted)

    def has_unexported_edits(self):
        """Animation exports cannot preserve blank keys or remembered static brightness."""
        return bool(self.unpainted) or (
            self.draft is not None
            and self.workspace_style == "animation"
            and self._static_brightness is not None
            and self._static_brightness != self.draft["brightness"]
        )

    def has_pending_edits(self):
        if self.unpainted or self.has_changes():
            return True

        if self.draft is None or self.workspace_style != "animation":
            return False

        captured = self._keyboard_preview
        return (self._static_brightness is not None and self._static_brightness != captured["brightness"]) or any(
            self.draft["slots"][index] != captured["slots"][index] for index in lighting.PHYSICAL
        )

    @property
    def pointer_selected(self):
        return self.workspace_style == "animation" or self.paint_tool == "pointer"

    def select_pointer(self):
        self.paint_tool = "pointer"

    def paint_clicked_key(self, index):
        if self.pointer_selected or self.draft is None or index not in lighting.PHYSICAL:
            return False

        if self.paint_tool == "bucket":
            self.flood_fill(index)
        else:
            self.paint_key(index)

        return True

    def select_brush(self, color):
        lighting.rgb(color)
        self.brush_color = color.upper()
        self.paint_tool = "brush" if self.workspace_style != "animation" else "pointer"

    def select_bucket(self, color):
        lighting.rgb(color)
        self.bucket_color = color.upper()
        self.paint_tool = "bucket" if self.workspace_style != "animation" else "pointer"

    def flood_fill(self, index):
        if self.draft is None or self.workspace_style == "animation" or index not in lighting.PHYSICAL:
            return

        def region_color(source):
            if source in self.unpainted:
                return None

            if self.draft["style"] == "solid":
                return self.draft["color"].upper()

            slot = self.draft["slots"][source]
            return slot["color"].upper() if slot["enabled"] else "#000000"

        original = region_color(index)
        targets, visited, pending = set(), set(), [index]
        while pending:
            source = pending.pop()
            if source in visited:
                continue

            visited.add(source)
            if region_color(source) != original:
                continue

            targets.add(source)
            pending.extend(KEY_NEIGHBORS[source] - visited)

        self._ensure_paint_layout()
        self._paint_targets(targets, enabled=self.bucket_color != "#000000", color=self.bucket_color, tool="bucket")

    def select_mode(self, mode):
        if self.draft is None:
            return

        if mode == 128:
            was_animation = self.workspace_style == "animation"
            self._ensure_paint_layout()
            if was_animation and self._static_brightness is not None:
                self.change(brightness=self._static_brightness)
        else:
            lighting.integer(mode, 7, 15)
            if self.workspace_style != "animation":
                self._static_brightness = self.draft["brightness"]
                self._ensure_paint_layout()

            settings = {"style": "animation", "mode": mode}
            if self.workspace_style != "animation" or self.draft["mode"] != mode:
                settings.update(brightness=16, generated=True, direction=0)

            self.change(**settings)

    def _ensure_paint_layout(self):
        if self.draft is None:
            raise ValueError("Read the keyboard before editing lighting")

        if self.draft["style"] == "solid":
            for index in lighting.PHYSICAL:
                self.draft["slots"][index] = {"enabled": True, "color": self.draft["color"]}

        self.change(style="perKey", color=self.brush_color)

    def clear_paint(self):
        if self.workspace_style == "animation":
            return

        self._ensure_paint_layout()
        self.unpainted = set(lighting.PHYSICAL)
        self.review = None
        self._revision += 1

    def fill_paint(self, *, overwriting=False):
        if self.workspace_style == "animation":
            return

        targets = lighting.PHYSICAL if overwriting else set(self.unpainted)
        if not targets:
            return

        self._ensure_paint_layout()
        color = self.bucket_color if self.paint_tool == "bucket" else self.brush_color
        tool = "bucket" if self.paint_tool == "bucket" else "brush"
        self._paint_targets(targets, enabled=color != "#000000", color=color, tool=tool)

    def paint_key(self, index):
        if self.draft is None or self.workspace_style == "animation" or index not in lighting.PHYSICAL:
            return

        self._ensure_paint_layout()
        self._paint_targets({index}, enabled=self.brush_color != "#000000")

    def has_key_change(self, index):
        if self.draft is None or self.workspace_style == "animation" or index not in lighting.PHYSICAL:
            return False

        if index in self.unpainted:
            return True

        def slot(draft):
            return {"enabled": True, "color": draft["color"]} if draft["style"] == "solid" else draft["slots"][index]

        return slot(self.preview_draft()) != slot(self._keyboard_preview)

    def revert_key(self, index):
        if self.draft is None or self.workspace_style == "animation" or index not in lighting.PHYSICAL:
            return

        self._ensure_paint_layout()
        current = self._keyboard_preview
        slot = {"enabled": True, "color": current["color"]} if current["style"] == "solid" else current["slots"][index]
        self.draft["slots"][index] = deepcopy(slot)
        self.unpainted.discard(index)
        self.review = None
        self._revision += 1

    def _paint_targets(self, targets, *, enabled, color=None, tool="brush"):
        color = self.brush_color if color is None else color
        self.review = None
        self._revision += 1
        for index in targets:
            slot = self.draft["slots"][index]
            slot["enabled"] = enabled
            if enabled:
                slot["color"] = color

        self.unpainted.difference_update(targets)
        if targets:
            self._remember_paint(color, tool)

    def _remember_paint(self, color, tool):
        last = self.last_bucket_color if tool == "bucket" else self.last_used_color
        if color in ("#000000", last):
            return

        if tool == "bucket":
            self.last_bucket_color = color
            path = self.bucket_history_path
        else:
            self.last_used_color = color
            path = self.history_path

        if path is None:
            return

        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".paint-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(color + "\n")

            temporary.replace(path)
        except OSError:
            logging.warning("Could not save the last-used lighting color", exc_info=True)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logging.warning("Could not remove a temporary lighting preference", exc_info=True)

    def preview_draft(self):
        if self.draft is None:
            return None

        return {**self.draft, "style": self.workspace_style,
            "color": self.brush_color if self.workspace_style == "perKey" else self.draft["color"]}

    def current_keyboard_preview(self):
        """Return the verified preview without exposing its mutable cache."""
        return deepcopy(self._keyboard_preview)

    def change(self, **fields):
        if self.draft is None:
            raise ValueError("Read the keyboard before editing lighting")

        self.review = None
        style = fields.get("style", self.workspace_style)
        if style == "perKey":
            if self.workspace_style != "perKey":
                self.brush_color = self.draft["color"]

            if "style" in fields:
                fields.setdefault("color", self.brush_color)

            if "color" in fields:
                self.brush_color = fields["color"]
                if self.draft["style"] != "perKey" and "style" not in fields:
                    # After Reset, choosing a brush must not recolor the captured animation.
                    fields.pop("color")

        if any(self.draft.get(name) != value for name, value in fields.items()):
            self._revision += 1

        self.draft.update(fields)
        if "style" in fields:
            self.workspace_style = fields["style"]
            if self.workspace_style == "animation":
                self.select_pointer()

    def paint(self, enabled=True):
        if self.draft is None:
            raise ValueError("Read the keyboard before editing lighting")

        self.change(style="perKey", color=self.brush_color)
        self._paint_targets(self.selection & lighting.PHYSICAL, enabled=enabled)

    def read(self):
        if self.service is None:
            raise ValueError("Offline captures cannot access the keyboard")

        try:
            state = self.service.read()
        except Exception:
            self.adopt(self.service.state())
            raise

        self.adopt(state)
        return state

    def prepare(self):
        self.review = None
        if self.has_unpainted():
            raise ValueError("Paint or fill every blank key before reviewing")

        if self.service is None:
            raise ValueError("Offline captures cannot be used for device writes")

        if self.draft is None:
            raise ValueError("Read the keyboard before preparing a change")

        self.review = self.service.plan(self.draft)  # The service owns the review snapshot.
        return self.review

    def apply(self, confirmed=False):
        if self.has_unpainted():
            self.review = None
            raise ValueError("Paint or fill every blank key before applying")

        if self.service is None or self.review is None or confirmed is not True:
            raise ValueError("Review and confirm the lighting change first")

        token = self.review["token"]
        self.review = None
        try:
            state = self.service.apply(token)
        except Exception:
            self.adopt(self.service.state())
            raise

        self.adopt(state)
        return state

    def snapshot_draft(self):
        """Copy a complete draft without serializing it or exposing editor state."""
        if self.has_unpainted():
            raise ValueError("Paint or fill every blank key before saving")

        if self.draft is None:
            raise ValueError("Read the keyboard before saving a draft")

        return deepcopy(self.draft)

    def export_draft(self):
        return encode_profile({"format": "c989-lighting-draft-v1", "settings": self.snapshot_draft()})

    def import_draft(self, text):
        if self.draft is None:
            raise ValueError("Read the keyboard before loading a draft")

        value = decode_lighting_draft(text)

        self.review = None
        self._revision += 1
        self.draft = value
        self.unpainted.clear()
        self._static_brightness = None
        self.workspace_style = self.draft["style"]
        if self.workspace_style == "animation":
            self.select_pointer()


def decode_lighting_draft(text):
    """Validate portable lighting settings without accessing a device or editor."""
    if len(text.encode()) > MAX_DRAFT_BYTES:
        raise ValueError("Lighting drafts must be smaller than 128 KB")

    data = json.loads(text)
    return parse_lighting_draft(data)


def parse_lighting_draft(data):
    """Validate an already decoded draft and return independent editor settings."""
    if not isinstance(data, dict) or data.get("format") != "c989-lighting-draft-v1":
        raise ValueError("Unsupported lighting draft")

    value = data.get("settings")
    if not isinstance(value, dict) or value.get("style") not in ("solid", "animation", "perKey"):
        raise ValueError("Invalid lighting draft")

    lighting.rgb(value.get("color"))
    lighting.integer(value.get("brightness"), 1 if value["style"] == "animation" else 0, 16)
    lighting.integer(value.get("mode"), 7, 15)
    lighting.integer(value.get("speed"), 2, 16)
    lighting.integer(value.get("direction"), 0, 1)
    if type(value.get("generated")) is not bool:
        raise ValueError("Invalid color palette")

    slots = value.get("slots")
    if not isinstance(slots, list) or len(slots) != 144:
        raise ValueError("Draft requires 144 lighting slots")

    for slot in slots:
        if not isinstance(slot, dict) or type(slot.get("enabled")) is not bool:
            raise ValueError("Invalid per-key slot")

        lighting.rgb(slot.get("color"))

    return deepcopy(value)
