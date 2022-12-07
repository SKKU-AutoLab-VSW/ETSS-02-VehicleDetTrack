# ==================================================================== #
# File name: aicity2021_main.py
# Author: Long H. Pham and Duong N.-N. Tran
# Date created: 03/27/2021
#
# The main run script.
# ==================================================================== #
import argparse
import os
from timeit import default_timer as timer

import multiprocessing

from tss.camera import CameraMultithread
from tss.io import compress_all_result
from tss.utils import data_dir
from tss.utils import prints
from tss.utils import process_config

import torch

# MARK: - Args

parser = argparse.ArgumentParser(description="Config parser")
parser.add_argument(
	"--dataset",
	default="aicity2021_final",
	help="The dataset to run on."
)
parser.add_argument(
	"--queue_size",
	default=10,
	type=int,
	help="The max queue size"
)
parser.add_argument(
	"--nodes",
	default=2,
	type=int,
	help="The number of nodes"
)
parser.add_argument(
	"--visualize",
	default=False,
	help="Should visualize the processed images"
)
parser.add_argument(
	"--write_video",
	default=False,
	help="Should write processed images to video"
)

config_files = [
	"cam_1.yaml",
	"cam_1_dawn.yaml",
	"cam_1_rain.yaml",
	"cam_2.yaml",
	"cam_2_rain.yaml",
	"cam_3.yaml",
	"cam_3_rain.yaml",
	"cam_4.yaml",
	"cam_4_dawn.yaml",
	"cam_4_rain.yaml",
	"cam_5.yaml",
	"cam_5_dawn.yaml",
	"cam_5_rain.yaml",
	"cam_6.yaml",
	"cam_6_snow.yaml",
	"cam_7.yaml",
	"cam_7_dawn.yaml",
	"cam_7_rain.yaml",
	"cam_8.yaml",
	"cam_9.yaml",
	"cam_10.yaml",
	"cam_11.yaml",
	"cam_12.yaml",
	"cam_13.yaml",
	"cam_14.yaml",
	"cam_15.yaml",
	"cam_16.yaml",
	"cam_17.yaml",
	"cam_18.yaml",
	"cam_19.yaml",
	"cam_20.yaml"
]


# MARK: - Main Function

def run_node(_, index_node):
	args = parser.parse_args()

	for index, config in enumerate(config_files):
		# DEBUG:
		# print(config)
		# print(f"{index} {args.nodes} {index_node}")
		# print(index % args.nodes == index_node)

		if index % args.nodes == index_node:

			# TODO: Start timer
			process_start_time = timer()
			total_camera_init_time = 0

			camera_start_time = timer()

			# TODO: Get camera config
			config_path = os.path.join(data_dir, args.dataset, "configs", config)
			camera_hprams = process_config(config_path=config_path)

			# TODO: Define camera
			camera = CameraMultithread(
				config      = camera_hprams,
				queue_size  = args.queue_size,
				visualize   = args.visualize,
				write_video = args.write_video
			)
			total_camera_init_time += timer() - camera_start_time
			# TODO: Process
			camera.run()

			# TODO: End timer
			total_process_time = timer() - process_start_time
			prints(f"Total processing time: {total_process_time} seconds.")
			prints(f"Total camera init time: {total_camera_init_time} seconds.")
			prints(f"Actual processing time: {total_process_time - total_camera_init_time} seconds.")


def main():
	args      = parser.parse_args()
	print(args)
	processes = []

	main_start_time = timer()

	# NOTE: Define processes
	for index_node in range(args.nodes):
		# DEBUG:
		# print(f"{index_node=}")
		# print(args.nodes)
		processes.append(multiprocessing.Process(target=run_node, args=([], index_node)))

	# NOTE: Start processes
	for process in processes:
		process.start()

	# NOTE: Wait all processes stop
	for process in processes:
		process.join()

	prints(f"************************************************")
	main_total_time = timer() - main_start_time
	prints(f"Main multiprocess Multithread time: {main_total_time} seconds.")

# # TODO: Compress result from tss.io import compress_all_result
# print("Compressing result")
# output_dir = os.path.join(data_dir, args.dataset, "outputs")
# compress_all_result(output_dir=output_dir)


# MARK: - Entry point

if __name__ == "__main__":
	main()