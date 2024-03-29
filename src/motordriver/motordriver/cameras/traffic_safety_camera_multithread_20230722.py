#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
"""

from __future__ import annotations

import os
import sys
import threading
import uuid
import glob
import random
from queue import Queue
from operator import itemgetter
from timeit import default_timer as timer
from typing import Union

import cv2
import torch
import numpy as np
from tqdm import tqdm

from core.data.class_label import ClassLabels
from core.io.filedir import is_basename
from core.io.filedir import is_json_file
from core.io.filedir import is_stem
from core.io.frame import FrameLoader
from core.io.frame import FrameWriter
from core.io.video import is_video_file
from core.io.video import VideoLoader
from core.io.picklewrap import PickleLoader
from core.utils.bbox import bbox_xyxy_to_cxcywh_norm
from core.utils.rich import console
from core.utils.constants import AppleRGB
from core.objects.instance import Instance
from core.factory.builder import CAMERAS, TRACKERS
from core.factory.builder import DETECTORS
from detectors.detector import BaseDetector
from trackers.tracker import Tracker
from configuration import (
	data_dir,
	config_dir
)
from cameras.base import BaseCamera

__all__ = [
	"TrafficSafetyCameraMultiThread"
]


# NOTE: only for ACI23_Track_5
classes_aic23 = ['motorbike', 'DHelmet', 'DNoHelmet', 'P1Helmet',
			   'P1NoHelmet', 'P2Helmet', 'P2NoHelmet']

# MARK: - TrafficSafetyCamera


# noinspection PyAttributeOutsideInit

@CAMERAS.register(name="traffic_safety_camera_multithread")
class TrafficSafetyCameraMultiThread(BaseCamera):

	# MARK: Magic Functions

	def __init__(
			self,
			data         : dict,
			dataset      : str,
			name         : str,
			detector     : dict,
			identifier   : dict,
			tracker      : dict,
			data_loader  : dict,
			data_writer  : Union[FrameWriter,  dict],
			process      : dict,
			id_          : Union[int, str] = uuid.uuid4().int,
			verbose      : bool            = False,
			queue_size   : int = 10,
			*args, **kwargs
	):
		"""

		Args:
			dataset (str):
				Dataset name. It is also the name of the directory inside
				`data_dir`.
			subset (str):
				Subset name. One of: [`dataset_a`, `dataset_b`].
			name (str):
				Camera name. It is also the name of the camera's config
				files.
			class_labels (ClassLabels, dict):
				ClassLabels object or a config dictionary.
			detector (BaseDetector, dict):
				Detector object or a detector's config dictionary.
			data_loader (FrameLoader, dict):
				Data loader object or a data loader's config dictionary.
			data_writer (VideoWriter, dict):
				Data writer object or a data writer's config dictionary.
			id_ (int, str):
				Camera's unique ID.
			verbose (bool):
				Verbosity mode. Default: `False`.
			queue_size (int):
				Size of queue store the information
		"""
		super().__init__(id_=id_, dataset=dataset, name=name)
		# NOTE: Init attributes
		self.start_time = None
		self.pbar       = None

		# NOTE: Define attributes
		self.process      = process
		self.verbose      = verbose

		self.data_cfg        = data
		self.detector_cfg    = detector
		self.identifier_cfg  = identifier
		self.tracker_cfg     = tracker
		self.data_loader_cfg = data_loader
		self.data_writer_cfg = data_writer

		self.init_dirs()
		self.init_data_loader(data_loader_cfg=self.data_loader_cfg)
		self.init_data_writer(data_writer_cfg=self.data_writer_cfg)
		self.init_class_labels(class_labels=self.detector_cfg['class_labels'])
		self.init_detector(detector=detector)
		self.init_identifier(identifier=identifier)
		self.init_tracker(tracker=tracker)

		# NOTE: Queue
		self.frames_queue          = Queue(maxsize = self.data_loader_cfg['queue_size'])
		self.detections_queue      = Queue(maxsize = self.detector_cfg['queue_size'])
		self.identifications_queue = Queue(maxsize = self.identifier_cfg['queue_size'])

	# MARK: Configure

	def init_dirs(self):
		"""Initialize dirs.

		Returns:

		"""
		self.root_dir    = os.path.join(data_dir)
		self.configs_dir = os.path.join(config_dir)
		self.outputs_dir = os.path.join(self.root_dir, self.data_writer_cfg["dst"])
		self.video_dir   = os.path.join(self.root_dir, self.data_loader_cfg["data"])
		self.image_dir   = os.path.join(self.root_dir, self.data_loader_cfg["data"])

	def init_class_labels(self, class_labels: Union[ClassLabels, dict]):
		"""Initialize class_labels.

		Args:
			class_labels (ClassLabels, dict):
				ClassLabels object or a config dictionary.
		"""
		if isinstance(class_labels, ClassLabels):
			self.class_labels = class_labels
		elif isinstance(class_labels, dict):
			file = class_labels["file"]
			if is_json_file(file):
				self.class_labels = ClassLabels.create_from_file(file)
			elif is_basename(file):
				file              = os.path.join(self.root_dir, file)
				self.class_labels = ClassLabels.create_from_file(file)
		else:
			file              = os.path.join(self.root_dir, f"class_labels.json")
			self.class_labels = ClassLabels.create_from_file(file)
			print(f"Cannot initialize class_labels from {class_labels}. "
				  f"Attempt to load from {file}.")

	def init_detector(self, detector: Union[BaseDetector, dict]):
		"""Initialize detector.

		Args:
			detector (BaseDetector, dict):
				Detector object or a detector's config dictionary.
		"""
		console.log(f"Initiate Detector.")
		if isinstance(detector, BaseDetector):
			self.detector = detector
		elif isinstance(detector, dict):
			detector["class_labels"] = self.class_labels
			self.detector = DETECTORS.build(**detector)
		else:
			raise ValueError(f"Cannot initialize detector with {detector}.")

	def init_identifier(self, identifier: Union[BaseDetector, dict]):
		"""Initialize identifier.

		Args:
			identifier (BaseDetector, dict):
				Identifier object or a identifier's config dictionary.
		"""
		console.log(f"Initiate Identifier.")
		if isinstance(identifier, BaseDetector):
			self.identifier = identifier
		elif isinstance(identifier, dict):
			identifier["class_labels"] = self.class_labels
			self.identifier = DETECTORS.build(**identifier)
		else:
			raise ValueError(f"Cannot initialize detector with {identifier}.")

	def init_tracker(self, tracker: Union[Tracker, dict]):
		"""Initialize tracker.

		Args:
			tracker (Tracker, dict):
				Tracker object or a tracker's config dictionary.
		"""
		console.log(f"Initiate Tracker.")
		if isinstance(tracker, Tracker):
			self.tracker = tracker
		elif isinstance(tracker, dict):
			self.tracker = TRACKERS.build(**tracker)
		else:
			raise ValueError(f"Cannot initialize detector with {tracker}.")

	def init_data_loader(self, data_loader_cfg: dict):
		"""Initialize data loader.

		Args:
			data_loader_cfg (dict):
				Data loader object or a data loader's config dictionary.
		"""
		if self.process["run_image"]:
			self.data_loader = FrameLoader(data=os.path.join(data_dir, data_loader_cfg["data_path"]), batch_size=data_loader_cfg["batch_size"])
		else:
			self.data_loader = VideoLoader(data=os.path.join(data_dir, data_loader_cfg["data_path"]), batch_size=data_loader_cfg["batch_size"])

		# NOTE: to keep track process
		self.pbar = tqdm(total=self.data_loader.num_frames, desc=f"{data_loader_cfg['data_path']}")

	def check_and_create_folder(self, attr, data_writer_cfg: dict):
		"""CHeck and create the folder to store the result

		Args:
			attr (str):
				the type of function/saving/creating
			data_writer_cfg (dict):
				configuration of camera
		Returns:
			None
		"""
		path = os.path.join(self.outputs_dir, f"{data_writer_cfg[attr]}")
		if not os.path.isdir(path):
			os.makedirs(path)
		data_writer_cfg[attr] = path

	def init_data_writer(self, data_writer_cfg: dict):
		"""Initialize data writer.

		Args:
			data_writer_cfg (FrameWriter, dict):
				Data writer object or a data writer's config dictionary.
		"""
		# NOTE: save detections crop
		data_writer_cfg["dets_crop_pkl"] = f'{data_writer_cfg["dets_crop_pkl"]}/{self.detector_cfg["folder_out"]}'
		self.check_and_create_folder("dets_crop_pkl", data_writer_cfg=data_writer_cfg)

	# MARK: Run

	def run_data_reader(self):
		for images, indexes, _, _ in self.data_loader:
			if len(indexes) == 0:
				break
			# NOTE: Push frame index and images to queue
			self.frames_queue.put([indexes, images])

		# NOTE: Push None to queue to act as a stopping condition for next thread
		self.frames_queue.put([None, None])

	def run_detector(self):
		"""Run detection model with videos
		"""
		# init value
		height_img, width_img = None, None

		# NOTE: run detection
		with torch.no_grad():  # phai them cai nay khong la bi memory leak
			while True:
				# NOTE: Get frame indexes and images from queue
				(indexes, images) = self.frames_queue.get()

				# if finish loading
				if indexes is None:
					break

				# get size of image
				if height_img is None:
					height_img, width_img, _ = images[0].shape

				# NOTE: Detect batch of instances
				batch_instances = self.detector.detect(
					indexes=indexes, images=images
				)

				# NOTE: Process the detection result
				for index_b, (index_image, batch) in enumerate(zip(indexes, batch_instances)):
				# for index_b, batch in enumerate(batch_instances):
					# store result each frame
					batch_detections = []

					for index_in, instance in enumerate(batch):
						bbox_xyxy = [int(i) for i in instance.bbox]
						crop_id   = [int(index_image), int(index_in)]

						# if size of bounding box is very small
						# because the heuristic need the bigger bounding box
						if abs(bbox_xyxy[2] - bbox_xyxy[0]) < 40 \
								or abs(bbox_xyxy[3] - bbox_xyxy[1]) < 40:
							continue

						# NOTE: crop the bounding box, add 60 or 1.5 scale
						bbox_xyxy  = scaleup_bbox(
							bbox_xyxy,
							height_img,
							width_img,
							ratio=1.5,
							padding=60
						)
						crop_image = images[index_b][bbox_xyxy[1]:bbox_xyxy[3], bbox_xyxy[0]:bbox_xyxy[2]]

						# transfer the result
						detection_result = {
							'video_name'  : self.data_loader_cfg['data_path'],
							'frame_index' : index_image,
							'image'       : crop_image,
							'bbox'        : (bbox_xyxy[0], bbox_xyxy[1], bbox_xyxy[2], bbox_xyxy[3]),
							'class_id'    : instance.class_label["train_id"],
							'id_'         : crop_id,
							'confidence'  : instance.confidence,
							'image_size'  : [width_img, height_img]
						}

						detection_instance = Instance(**detection_result)
						batch_detections.append(detection_instance)

					# NOTE: Push detections to queue
					self.detections_queue.put([index_image, images[index_b], batch_detections])

				# self.pbar.update(len(indexes))

		# NOTE: Push None to queue to act as a stopping condition for next thread
		self.detections_queue.put([None, None, None])

	def run_identifier(self):
		"""Run identification model

		Returns:

		"""
		# NOTE: Run identification
		with torch.no_grad():  # phai them cai nay khong la bi memory leak
			while True:
				# NOTE: Get batch detections from queue
				(index_frame, frame, batch_detections) = self.detections_queue.get()

				if batch_detections is None:
					break

				# Load crop images
				crop_images = []
				indexes = []
				for detection_instance in batch_detections:
					crop_images.append(detection_instance.image)
					indexes.append(detection_instance.id_[1])

				# NOTE: Identify batch of instances
				batch_instances = self.identifier.detect(
					indexes=indexes, images=crop_images
				)

				# store result each crop image
				batch_identifications = []

				# NOTE: Process the full identify result
				for index_b, (detection_instance, batch_instance) in enumerate(zip(batch_detections, batch_instances)):
					for index_in, instance in enumerate(batch_instance):
						bbox_xyxy     = [int(i) for i in instance.bbox]
						instance_id   = detection_instance.id_.append(int(index_in))

						# NOTE: add the coordinate from crop image to original image
						# DEBUG: comment doan nay neu extract anh nho
						bbox_xyxy[0] += int(detection_instance.bbox[0])
						bbox_xyxy[1] += int(detection_instance.bbox[1])
						bbox_xyxy[2] += int(detection_instance.bbox[0])
						bbox_xyxy[3] += int(detection_instance.bbox[1])

						# if size of bounding box0 is very small
						if abs(bbox_xyxy[2] - bbox_xyxy[0]) < 40 \
								or abs(bbox_xyxy[3] - bbox_xyxy[1]) < 40:
							continue

						# NOTE: crop the bounding box, add 60 or 1.5 scale
						bbox_xyxy = scaleup_bbox(
							bbox_xyxy,
							detection_instance.image_size[1],
							detection_instance.image_size[0],
							ratio=2.0,
							padding=60
						)
						# instance_image = frame[bbox_xyxy[1]:bbox_xyxy[3], bbox_xyxy[0]:bbox_xyxy[2]]

						identification_result = {
							'video_name'    : detection_instance.video_name,
							'frame_index'   : detection_instance.frame_index,
							'bbox'          : np.array((bbox_xyxy[0], bbox_xyxy[1], bbox_xyxy[2], bbox_xyxy[3])),
							'class_id'      : instance.class_label["train_id"],
							'id'            : instance_id,
							'confidence'    : (float(detection_instance.confidence) * instance.confidence),
							'image_size'    : detection_instance.image_size
						}

						identification_instance = Instance(**identification_result)
						batch_identifications.append(identification_instance)

						# DEBUG: write the instance object (not for motorbike, only for driver and passenger)
						# if instance.class_label["train_id"] > 0:
						# 	cv2.imwrite(
						# 		f"/media/sugarubuntu/DataSKKU3/3_Dataset/AI_City_Challenge/2023/Track_5/aicity2023_track5_test_docker/output_aic23/dets_crop_debug/{batch_identifications[0].video_name}_instances/" \
						# 		f"{batch_identifications[0].frame_index:04d}_{index_in:04d}_{instance.class_label['train_id']}.jpg",
						# 		instance_image
						# 	)

				# NOTE: Push identifications to queue
				self.identifications_queue.put([index_frame, frame, batch_identifications])

				# self.pbar.update(1)

		# NOTE: Push None to queue to act as a stopping condition for next thread
		self.identifications_queue.put([None, None, None])

	def run_tracker(self):
		with torch.no_grad():  # phai them cai nay khong la bi memory leak
			while True:
				# NOTE: Get batch identification from queue
				(index_frame, frame, batch_identifications) = self.identifications_queue.get()

				if batch_identifications is None:
					break

				# DEBUG:
				# image_draw = frame.copy()

				# NOTE: Track (in batch)
				self.tracker.update(detections=batch_identifications)
				gmos = self.tracker.tracks

				# NOTE: Update moving state
				for gmo in gmos:
					# gmo.update_moving_state(rois=self.rois)
					gmo.timestamps.append(timer())

					# DEBUG:
					# print(dir(gmo))
					# # print(gmo.current_label)
					# plot_one_box(
					# 	bbox = gmo.bboxes[-1],
					# 	img  = image_draw,
					# 	color= AppleRGB.values()[gmo.current_label],
					# 	label= f"{classes_aic23[gmo.current_label]}::{gmo.id % 1000}"
					# )

				# DEBUG:
				# cv2.imwrite(
				# 	f"/media/sugarubuntu/DataSKKU3/3_Dataset/AI_City_Challenge/2023/Track_5/aicity2023_track5_test_docker/output_aic23/dets_crop_debug/{batch_identifications[0].video_name}/" \
				# 	f"{batch_identifications[0].frame_index:04d}.jpg",
				# 	image_draw
				# )

				self.pbar.update(1)

	def run_matching(self):
		with torch.no_grad():  # phai them cai nay khong la bi memory leak
			pass

	def run(self):
		"""Main run loop."""
		self.run_routine_start()

		# NOTE: Threading for data reader
		thread_data_reader = threading.Thread(target=self.run_data_reader)
		thread_data_reader.start()

		# NOTE: Threading for detector
		thread_detector = threading.Thread(target=self.run_detector)
		thread_detector.start()

		# NOTE: Threading for identifier
		thread_identifier = threading.Thread(target=self.run_identifier)
		thread_identifier.start()

		# NOTE: Threading for tracker
		thread_tracker = threading.Thread(target=self.run_tracker)
		thread_tracker.start()

		# NOTE: Threading for matching
		thread_matching = threading.Thread(target=self.run_tracker)
		thread_matching.start()

		# NOTE: Joins threads when all terminate
		thread_data_reader.join()
		thread_detector.join()
		thread_identifier.join()
		thread_tracker.join()
		thread_matching.join()

		self.run_routine_end()

	def run_routine_start(self):
		"""Perform operations when run routine starts. We start the timer."""
		self.start_time = timer()
		if self.verbose:
			cv2.namedWindow(self.name, cv2.WINDOW_KEEPRATIO)

	def run_routine_end(self):
		"""Perform operations when run routine ends."""
		# NOTE: clear detector
		self.detector.clear_model_memory()
		self.detector = None

		cv2.destroyAllWindows()
		self.stop_time = timer()
		if self.pbar is not None:
			self.pbar.close()

	def postprocess(self, image: np.ndarray, *args, **kwargs):
		"""Perform some postprocessing operations when a run step end.

		Args:
			image (np.ndarray):
				Image.
		"""
		if not self.verbose and not self.save_image and not self.save_video:
			return

		elapsed_time = timer() - self.start_time
		if self.verbose:
			# cv2.imshow(self.name, result)
			cv2.waitKey(1)

	# MARK: Visualize

	def draw(self, drawing: np.ndarray, elapsed_time: float) -> np.ndarray:
		"""Visualize the results on the drawing.

		Args:
			drawing (np.ndarray):
				Drawing canvas.
			elapsed_time (float):
				Elapsed time per iteration.

		Returns:
			drawing (np.ndarray):
				Drawn canvas.
		"""
		return drawing


# MARK - Ultilies

def scaleup_bbox(bbox_xyxy, height_img, width_img, ratio, padding):
	"""Scale up 1.2% or +-40

	Args:
		bbox_xyxy:
		height_img:
		width_img:

	Returns:

	"""
	cx = 0.5 * bbox_xyxy[0] + 0.5 * bbox_xyxy[2]
	cy = 0.5 * bbox_xyxy[1] + 0.5 * bbox_xyxy[3]
	w = abs(bbox_xyxy[2] - bbox_xyxy[0])
	w = min(w * ratio, w + padding)
	h = abs(bbox_xyxy[3] - bbox_xyxy[1])
	h = min(h * ratio, h + padding)
	bbox_xyxy[0] = int(max(0, cx - 0.5 * w))
	bbox_xyxy[1] = int(max(0, cy - 0.5 * h))
	bbox_xyxy[2] = int(min(width_img - 1, cx + 0.5 * w))
	bbox_xyxy[3] = int(min(height_img - 1, cy + 0.5 * h))
	return bbox_xyxy


def plot_one_box(bbox, img, color=None, label=None, line_thickness=1):
	"""Plots one bounding box on image img

	Returns:

	"""
	tl = line_thickness or round(0.002 * (img.shape[0] + img.shape[1]) / 2) + 1  # line/font thickness
	color = color or [random.randint(0, 255) for _ in range(3)]
	c1, c2 = (int(bbox[0]), int(bbox[1])), (int(bbox[2]), int(bbox[3]))
	cv2.rectangle(img, c1, c2, color, thickness=tl, lineType=cv2.LINE_AA)
	if label:
		tf = max(tl - 1, 1)  # font thickness
		t_size = cv2.getTextSize(label, 0, fontScale=tl / 3, thickness=tf)[0]
		c2 = c1[0] + t_size[0], c1[1] - t_size[1] - 3
		cv2.rectangle(img, c1, c2, color, -1, cv2.LINE_AA)  # filled
		cv2.putText(img, label, (c1[0], c1[1] - 2), 0, tl / 3, [225, 255, 255], thickness=tf, lineType=cv2.LINE_AA)
