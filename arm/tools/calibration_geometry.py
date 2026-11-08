"""Offline geometry for the pinned SDK motor-coordinate model and card observations.

This is the SDK gravity model's raw-motor frame convention, not a claim of
hardware calibration. Fits must pass held-out pose validation before use.
"""
import math
import cv2
import numpy as np
from maker_arm import gravity


class IncrementalPlanarTracker:
    """Track small inter-frame changes, retaining correspondences to the first image."""
    def __init__(self, image, corners):
        cv2.setNumThreads(1)
        self.gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        self.corners=np.asarray(corners,dtype=np.float32).reshape(4,2)
        mask=np.zeros(self.gray.shape,np.uint8)
        cv2.fillConvexPoly(mask,np.rint(self.corners).astype(np.int32),255)
        mask=cv2.erode(mask,np.ones((5,5),np.uint8))
        self.points=cv2.goodFeaturesToTrack(self.gray,150,.01,3,mask=mask)
        if self.points is None or len(self.points)<12:raise RuntimeError('Too few initial tracking corners')
        self.original=self.points.copy()
        self.ids=np.arange(len(self.points))

    def update(self,image):
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        if gray.shape!=self.gray.shape:raise RuntimeError('Camera dimensions changed while tracking')
        criteria=(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,30,.01)
        points,status,_=cv2.calcOpticalFlowPyrLK(self.gray,gray,self.points,None,winSize=(21,21),maxLevel=3,criteria=criteria)
        if points is None:raise RuntimeError('Optical flow failed')
        back,reverse,_=cv2.calcOpticalFlowPyrLK(gray,self.gray,points,None,winSize=(21,21),maxLevel=3,criteria=criteria)
        if back is None:raise RuntimeError('Reverse optical flow failed')
        fb=np.linalg.norm(back[:,0]-self.points[:,0],axis=1)
        good=(status.ravel()>0)&(reverse.ravel()>0)&(fb<1.)
        if good.sum()<12:raise RuntimeError('Too few forward/backward-consistent marker tracks')
        H,inliers=cv2.findHomography(self.original[good],points[good],cv2.RANSAC,2.)
        if H is None or inliers is None or inliers.sum()<12:raise RuntimeError('Marker tracks do not support a planar mapping')
        keep=np.flatnonzero(good)[inliers.ravel().astype(bool)]
        projected=cv2.perspectiveTransform(self.original[keep],H)
        error=np.linalg.norm(projected[:,0]-points[keep,0],axis=1)
        corners=cv2.perspectiveTransform(self.corners[None],H)[0]
        if not np.isfinite(corners).all() or not cv2.isContourConvex(corners):raise RuntimeError('Invalid tracked marker polygon')
        coverage=cv2.contourArea(cv2.convexHull(self.original[keep]))/abs(cv2.contourArea(self.corners))
        if coverage<.15:raise RuntimeError('Marker tracks lack spatial coverage')
        self.points=points[keep];self.original=self.original[keep];self.ids=self.ids[keep];self.gray=gray
        return {'corners_px':corners.tolist(),'inliers':len(keep),
                'median_inlier_error_px':float(np.median(error)),
                'max_forward_backward_error_px':float(fb[keep].max()),'initial_feature_coverage':coverage,
                'features':[{'id':int(i),'initial_px':o[0].tolist(),'image_px':p[0].tolist()}
                            for i,o,p in zip(self.ids,self.original,self.points)]}


class PlanarFeatureTracker:
    """Track a selected planar texture; physical dimensions are supplied separately, if known."""
    def __init__(self, template):
        cv2.setNumThreads(1)
        self.template = cv2.imread(str(template), cv2.IMREAD_GRAYSCALE)
        if self.template is None:
            raise ValueError('Feature template missing')
        self.sift = cv2.SIFT_create(contrastThreshold=.015)
        self.keypoints, self.descriptors = self.sift.detectAndCompute(self.template, None)
        if self.descriptors is None or len(self.keypoints) < 20:
            raise ValueError('Feature template lacks features')

    def detect(self, image, roi=None):
        if image is None:
            raise ValueError('Missing camera image')
        h, w = image.shape[:2]
        x0, y0, x1, y1 = roi or (0, 0, w, h)
        crop = image[y0:y1, x0:x1]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        keypoints, descriptors = self.sift.detectAndCompute(gray, None)
        if descriptors is None or len(keypoints) < 12:
            raise RuntimeError('No reliable marker features')
        pairs = cv2.BFMatcher().knnMatch(self.descriptors, descriptors, k=2)
        matches = [a for pair in pairs if len(pair) == 2 for a, b in [pair] if a.distance < .7*b.distance]
        if len(matches) < 12:
            raise RuntimeError('Too few unique marker matches')
        source = np.float32([self.keypoints[m.queryIdx].pt for m in matches])
        destination = np.float32([keypoints[m.trainIdx].pt for m in matches]) + [x0, y0]
        H, mask = cv2.findHomography(source, destination, cv2.RANSAC, 2.5)
        if H is None or mask is None or np.count_nonzero(mask) < 12:
            raise RuntimeError('Marker homography not supported by enough inliers')
        selected = mask.ravel().astype(bool)
        coverage = cv2.contourArea(cv2.convexHull(source[selected]))/self.template.size
        if coverage < .15:
            raise RuntimeError('Marker matches cover too little of the reference')
        th, tw = self.template.shape
        corners = cv2.perspectiveTransform(np.float32([[[0,0],[tw,0],[tw,th],[0,th]]]),H)[0]
        if (not np.isfinite(corners).all() or not cv2.isContourConvex(corners)
                or abs(cv2.contourArea(corners)) < 1200
                or np.any(corners < 3) or np.any(corners[:,0] > w-4) or np.any(corners[:,1] > h-4)):
            raise RuntimeError('Marker not fully visible or geometry invalid')
        predicted = cv2.perspectiveTransform(source[selected,None,:],H)[:,0,:]
        error = np.linalg.norm(predicted-destination[selected],axis=1)
        return {'corners_px': corners.tolist(), 'inliers': int(selected.sum()),
                'median_inlier_error_px': float(np.median(error)),
                'template_inlier_coverage': coverage, 'image_size_px': [w,h]}


def raw_motor_fk(q_deg):
    q = np.asarray(q_deg, dtype=float)
    if q.shape != (6,) or not np.isfinite(q).all():
        raise ValueError('Six finite raw motor degrees required')
    transform = np.eye(4)
    transform[:3, :3] = gravity._ROOT
    for angle, (xyz, rotation, axis) in zip(q, gravity._FIXED):
        fixed = np.eye(4); fixed[:3, :3] = rotation; fixed[:3, 3] = xyz
        moving = np.eye(4)
        moving[:3, :3] = gravity._about_z(axis[2] * math.radians(angle))
        transform = transform @ fixed @ moving
    return transform


def ordered_quad(points):
    points = np.asarray(points, dtype=float).reshape(4, 2)
    center = points.mean(axis=0)
    order = np.argsort(np.arctan2(points[:, 1]-center[1], points[:, 0]-center[0]))
    points = points[order]
    return np.roll(points, -np.argmin(points.sum(axis=1)), axis=0)


def card_candidates(image, roi=None):
    """Return quadrilateral candidates containing red pips; caller validates identity."""
    cv2.setNumThreads(1)
    if image is None:
        raise ValueError('Image could not be read')
    h, w = image.shape[:2]
    x0, y0, x1, y1 = roi or (0, 0, w, h)
    if not 0 <= x0 < x1 <= w or not 0 <= y0 < y1 <= h:
        raise ValueError('Invalid card search region')
    crop = image[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    red = (((hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 165)) &
           (hsv[:, :, 1] > 75) & (hsv[:, :, 2] > 60)).astype(np.uint8)*255
    edges = cv2.Canny(gray, 30, 90)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        quad = cv2.approxPolyDP(contour, .025*perimeter, True)
        if len(quad) != 4 or not cv2.isContourConvex(quad):
            continue
        area = abs(cv2.contourArea(quad))
        if area < 1200 or area > .7*crop.shape[0]*crop.shape[1]:
            continue
        mask = np.zeros(gray.shape, dtype=np.uint8); cv2.fillConvexPoly(mask, quad, 255)
        red_fraction = np.count_nonzero(red & mask)/max(1, np.count_nonzero(mask))
        brightness = float(gray[mask > 0].mean())
        if not .015 < red_fraction < .45 or brightness < 110:
            continue
        corners = ordered_quad(quad) + [x0, y0]
        if any(np.linalg.norm(corners - c['corners_px']) < 8 for c in candidates):
            continue
        candidates.append({'corners_px': corners.tolist(), 'area_px': area,
                           'red_fraction': red_fraction, 'mean_brightness': brightness})
    return sorted(candidates, key=lambda c: -c['area_px'])
