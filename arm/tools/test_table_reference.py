import numpy as np
import pytest
import table_reference as reference


def observation():
    return {'reference_plane': 'table', 'reference_is_flat': True,
            'reference_size_mm': [63, 88], 'dimension_source': 'assumed',
            'reference_corners_px': [[10, 10], [73, 10], [73, 98], [10, 98]],
            'table_points_px': {'cylinder_foot': [100, 100]},
            'image_size_px': [640, 480]}


def test_known_perspective_mapping():
    world = np.array([[0, 0], [63, 0], [63, 88], [0, 88]])
    camera = np.array([[2., .1, 50], [.2, 1.5, 40], [.001, .002, 1.]])
    image = reference.project(camera, world)
    inverse = reference.homography(image, world)
    heldout = np.array([[30, 40], [90, 50]])
    np.testing.assert_allclose(reference.project(inverse, reference.project(camera, heldout)), heldout)


@pytest.mark.parametrize('corners', [[[0, 0]]*4,
    [[0, 0], [1, 1], [0, 1], [1, 0]], [[0, 0], [1, 0], [2, 0], [3, 0]]])
def test_invalid_geometry(corners):
    with pytest.raises(ValueError):
        reference.homography(corners, [[0, 0], [63, 0], [63, 88], [0, 88]])


def test_observation_never_claims_robot_registration():
    result = reference.measure(observation())
    np.testing.assert_allclose(result['table_points']['cylinder_foot']['xy_mm'], [90, 90])
    assert result['robot_base_transforms'] is None and not result['motion_ready']
    interval = result['table_points']['cylinder_foot']['annotation_only_95pct_interval_mm']
    assert interval[0][0] < 90 < interval[1][0]


def test_upright_reference_and_off_image_annotations_rejected():
    o = observation(); o['reference_is_flat'] = False
    with pytest.raises(ValueError, match='flat'):
        reference.measure(o)
    o = observation(); o['table_points_px']['cylinder_foot'] = [700, 500]
    with pytest.raises(ValueError, match='outside image'):
        reference.measure(o)
