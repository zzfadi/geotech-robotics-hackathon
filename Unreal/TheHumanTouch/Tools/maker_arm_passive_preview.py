"""Camera-only previews for unselected fleet tables; no actors or physics are driven."""
import json
import time
from pathlib import Path


def passive_indices(selected):
    if any(type(i) is not int or not 0 <= i <= 8 for i in selected):
        raise ValueError('Expected nine cup table identities only')
    return [i for i in range(9) if i not in selected]


def due_camera(items, now):
    due = [(item['next_capture'], index) for index, item in items.items()
           if not item.get('pending') and not item.get('error') and item['next_capture'] <= now]
    return min(due)[1] if due else None


def review_candidates(tables, now):
    """A failed policy/guard must not freeze its camera; pending packets matter
    only for healthy tables. This does not resume observation or motion work.
    """
    return sorted((table for table in tables
                   if 'overhead' in table.captures
                   and (table.error or not table.pending)
                   and now >= table.next_capture), key=lambda table: table.next_capture)


class PassivePreview:
    def __init__(self, selected, root):
        self.indices = passive_indices(selected)
        self.root = Path(root)/'passive-preview'
        self.root.mkdir(parents=True, exist_ok=True)
        self.items = {}
        self.frames, self.frame_times, self.errors = {}, {}, {}

    def spawn(self, editor_actors, subsystem, tags, local):
        import unreal
        from maker_arm_fleet_layout import label
        for index in self.indices:
            source_name = label(index, 'MakerCamera_Overview')
            camera = editor_actors.get(source_name)
            if camera is None:
                self.errors['T%02d' % index] = 'Missing passive overhead camera'
                continue
            actor = subsystem.spawn_actor_from_class(unreal.SceneCapture2D, camera.get_actor_location(), camera.get_actor_rotation(), False)
            local.append(actor)
            actor.set_actor_label(label(index, 'FleetCapture_PassiveOverview'))
            actor.set_editor_property('tags', tags)
            for primitive in actor.get_components_by_class(unreal.PrimitiveComponent):
                primitive.set_visibility(False, False)
                primitive.set_hidden_in_game(True, False)

    def bind(self, found):
        import unreal
        from maker_arm_fleet_layout import label
        for index in self.indices:
            source = found.get(label(index, 'MakerCamera_Overview'))
            actor = found.get(label(index, 'FleetCapture_PassiveOverview'))
            if source is None or actor is None:
                self.errors['T%02d' % index] = 'Passive camera unavailable in SIE'
                continue
            target = unreal.new_object(unreal.TextureRenderTarget2D)
            target.set_editor_property('render_target_format', unreal.TextureRenderTargetFormat.RTF_RGBA8)
            target.set_editor_property('target_gamma', 2.2)
            unreal.RenderingLibrary.resize_render_target2d(target, 320, 176)
            capture = actor.get_component_by_class(unreal.SceneCaptureComponent2D)
            for key, value in [('texture_target', target), ('capture_source', unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR),
                               ('capture_every_frame', False), ('capture_on_movement', False), ('always_persist_rendering_state', True),
                               ('fov_angle', source.get_component_by_class(unreal.CameraComponent).field_of_view)]:
                capture.set_editor_property(key, value)
            self.items[index] = {'actor': actor, 'capture': capture, 'target': target, 'source': source,
                                 'next_capture': 0., 'pending': None, 'sequence': 0}

    def tick(self, now, budget, export):
        # Complete asynchronous writes before advertising timestamps or reusing files.
        changed = False
        for index, item in self.items.items():
            pending = item['pending']
            if pending:
                try: data = pending['path'].read_bytes()
                except OSError: data = b''
                if data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
                    key = 'T%02d' % index
                    self.frames[key] = str(pending['path'])
                    self.frame_times[key] = pending['captured_unix']
                    item['pending'] = None
                    changed = True
                elif now-pending['started'] > 2:
                    item['error'] = 'Passive camera export timed out'
                    self.errors['T%02d' % index] = item['error']
                    item['pending'] = None
                    changed = True
        if changed:
            path = self.root/'frames.tmp'
            path.write_text(json.dumps({'scope':'passive-sie-camera-only','frames':self.frames,
                                        'frame_times':self.frame_times,'errors':self.errors}))
            path.replace(self.root/'frames.json')
        for _ in range(max(0, budget)):
            index = due_camera(self.items, now)
            if index is None: break
            item = self.items[index]
            item['sequence'] += 1
            # Two slots preserve the previous complete frame while the next is written.
            path = self.root/('T%02d-%d.jpg' % (index, item['sequence'] % 2))
            try:
                stamp = time.time()
                export(item, path)
                item['pending'] = {'path':path,'captured_unix':stamp,'started':now}
                item['next_capture'] = now+1.
            except Exception as error:
                item['error'] = str(error)
                self.errors['T%02d' % index] = str(error)
