"""Confirmed physical units, checked against existing world geometry.

No unit labels or source objects are mutated. Child proxy coordinates are
intrinsic meters; one root scale converts meters to scene Blender units.
"""
import math


def positive(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(field + ' must be a finite positive number')
    return float(value)


def meters_to_scene(values, meters_per_blender_unit):
    unit = positive(meters_per_blender_unit, 'meters_per_blender_unit')
    if isinstance(values, (int, float)) and not isinstance(values, bool):
        if not math.isfinite(values): raise ValueError('Position must be finite')
        return values / unit
    result = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError('Position must be finite')
        result.append(v / unit)
    return tuple(result)


def mesh_world_bounds(objects):
    """Actual mesh vertices, including every object/parent transform; not labels."""
    vertices = [ob.matrix_world @ vertex.co for ob in objects if ob.type == 'MESH' for vertex in ob.data.vertices]
    if not vertices: raise ValueError('Calibration or actor has no mesh vertices')
    lo = [min(v[i] for v in vertices) for i in range(3)]
    hi = [max(v[i] for v in vertices) for i in range(3)]
    return {'min': lo, 'max': hi, 'dimensions': [hi[i] - lo[i] for i in range(3)]}


def descendants(root):
    result = []
    for ob in root.children:
        result.append(ob)
        result.extend(descendants(ob))
    return result


def world_bounds_m(root, meters_per_blender_unit):
    unit = positive(meters_per_blender_unit, 'meters_per_blender_unit')
    bounds = mesh_world_bounds(descendants(root))
    return {key: [float(x) * unit for x in values] for key, values in bounds.items()}


def apply_intrinsic_meter_scale(root, meters_per_blender_unit):
    """Apply exactly once; reject accidental double conversion even if identical."""
    unit = positive(meters_per_blender_unit, 'meters_per_blender_unit')
    if root.get('intrinsic_meter_conversion_applied', False):
        raise ValueError('Intrinsic meter conversion already applied; refusing double scaling')
    if any(abs(float(v)-1) > 1e-7 for v in root.scale):
        raise ValueError('Proxy root already scaled; physical conversion requires identity scale')
    root.scale = (1 / unit,) * 3
    root['intrinsic_meter_conversion_applied'] = True
    root['meters_per_blender_unit'] = unit
    root['intrinsic_coordinates'] = 'meters'
    return root


def validate_scene_scale(scene, unit_record):
    """Validate a persisted, confirmed scale using actual calibration mesh extent.

    unit_record = {status:'confirmed', meters_per_blender_unit:0.01,
      source:'user-confirmed ruler', calibration:{object:'ScaleBar',axis:'x',
      length_m:2.0,tolerance_m:0.00001}}
    The named mesh may be a real architectural feature whose physical extent
    is known. A scale label alone, an actor, or a generated preview is no proof.
    """
    if not isinstance(unit_record, dict) or unit_record.get('status') != 'confirmed':
        raise ValueError('Scene scale unconfirmed: ask for units and a known calibration length')
    if not isinstance(unit_record.get('source'), str) or not unit_record['source'].strip():
        raise ValueError('Scene scale confirmation source is required')
    unit = positive(unit_record.get('meters_per_blender_unit'), 'meters_per_blender_unit')
    actual_label = positive(scene.unit_settings.scale_length, 'scene.unit_settings.scale_length')
    if not math.isclose(actual_label, unit, rel_tol=1e-6, abs_tol=1e-10):
        raise ValueError('Scene unit label conflicts with confirmed physical units; resolve before rendering')
    calibration = unit_record.get('calibration')
    if not isinstance(calibration, dict): raise ValueError('Physical calibration is required; unit labels are insufficient')
    axis = str(calibration.get('axis', '')).lower()
    if axis not in ('x', 'y', 'z'): raise ValueError('Calibration world axis must be x, y or z')
    object_name = calibration.get('object')
    if not isinstance(object_name, str) or not object_name: raise ValueError('Named calibration object is required')
    ob = scene.objects.get(object_name)
    if ob is None or ob.type != 'MESH': raise ValueError('Calibration object must be an existing scene mesh: ' + object_name)
    if ob.get('part_id') or ob.get('registry_character_id') or ob.get('generator_version'):
        raise ValueError('Character proxy cannot calibrate its own scene')
    if ob.modifiers: raise ValueError('Calibration mesh has modifiers; use a reviewed evaluated mesh or another reference')
    expected = positive(calibration.get('length_m'), 'calibration.length_m')
    tolerance = positive(calibration.get('tolerance_m', 1e-5), 'calibration.tolerance_m')
    if tolerance > expected * .01: raise ValueError('Calibration tolerance cannot exceed one percent of known length')
    bounds = mesh_world_bounds([ob]); extent = bounds['dimensions']['xyz'.index(axis)]
    actual = extent * unit
    if abs(actual - expected) > tolerance:
        raise ValueError('Scene calibration mismatch: actual world extent %.9g BU * %.9g m/BU = %.9g m; expected %.9g m' % (extent, unit, actual, expected))
    return {'meters_per_blender_unit': unit, 'calibration_object': object_name,
            'calibration_axis': axis, 'extent_blender_units': extent,
            'expected_length_m': expected, 'actual_length_m': actual,
            'absolute_error_m': abs(actual-expected), 'tolerance_m': tolerance,
            'source': unit_record['source'], 'scene_geometry_mutated': False}
