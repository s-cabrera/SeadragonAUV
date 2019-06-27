#!/usr/bin/env python

import numpy as np
import rospy
import smach
import smach_ros
from std_msgs.msg import Bool, Float64, Int16

CAMERA_WIDTH = 400
CAMERA_HEIGHT = 300
CENTER_PADDING_X = 5
CENTER_PADDING_Y = 5
YAW_INCREASE = 0.017 # radians
DEPTH_STEP = 1
FORWARD_THRUST_INCREASE = 1
AREA_THRESHOLD_LOW = 0.85
AREA_THRESHOLD_HIGH = 0.90
TORPEDO_Y_OFFSET = 10
MAX_FORWARD_THRUST= 280

class StartState(smach.State):
	def __init__(self):
		smach.State.__init__(self, outcomes=['ready', 'notready'])

		self.torpedo_task_enabled = False
		rospy.Subscriber('/torpedo_enable', Bool, self.task_enable_callback)

	def task_enable_callback(self, msg):
		self.torpedo_task_enabled = msg.data

	def execute(self, userdata):
		if self.torpedo_task_enabled:
			self.torpedo_task_enabled = False
			return 'ready'
		else:
			return 'notready'

class TrackObjectState(smach.State):
	def __init__(self, obj_topic, yoffset):
		smach.State.__init__(self, outcomes=['completed', 'notcompleted', 'reset'])

		self.yoffset = yoffset
		self.timer = 0

		self.object_x = 0 # in pixels
		self.object_y = 0 # in pixels
		self.object_area = 0 # object width * height
		rospy.Subscriber(obj_topic['x'], Float64, self.object_x_callback)
		rospy.Subscriber(obj_topic['y'], Float64, self.object_y_callback)
		rospy.Subscriber(obj_topic['area'], Float64, self.object_area_callback)
		
		self.yaw_current = 0 # in degrees
		rospy.Subscriber('/yaw_control/state', Float64, self.yaw_callback) # current orientation
		self.yaw_publisher = rospy.Publisher('yaw_control/setpoint', Float64, queue_size=10) # desired orientation

		self.depth_current = 0 # in inches
		rospy.Subscriber('/depth_control/state', Float64, self.depth_callback)
		self.depth_publisher = rospy.Publisher('/depth_control/setpoint', Float64, queue_size=10)

		self.forward_thrust_publisher = rospy.Publisher('/yaw_pwm', Int16, queue_size=10)
		self.forward_thrust = 0

		self.has_reset = False
		self.reset_subscriber = rospy.Subscriber('/reset', Bool, self.reset_callback)

	def object_x_callback(self, msg):
		self.object_x = msg.data
	def object_y_callback(self, msg):
		self.object_y = msg.data
	def object_area_callback(self, msg):
		self.object_area = msg.data
	def yaw_callback(self, msg):
		self.yaw_current = msg.data
	def depth_callback(self, msg):
		self.depth_current = msg.data
	def reset_callback(self, msg):
		self.has_reset = msg.data

	def execute(self, userdata):
		self.timer = self.timer + 1
		if self.has_reset:
			self.resetValues()
			return 'reset'

		is_object_x_centered = self.adjust_yaw() 
		is_object_y_centered = self.adjust_depth()
		is_object_area_in_threshold = False

		if is_object_x_centered and is_object_y_centered:
			is_object_area_in_threshold = self.adjust_position() 

		# go to next state if the object is at the center of the camera frame and within certain distace of the submarine
		if is_object_x_centered and is_object_y_centered and is_object_area_in_threshold:
			self.resetValues()
			return 'completed'
		elif
			return 'notcompleted'

	def resetValues(self):
		self.object_x = 0 # in pixels
		self.object_y = 0
		self.object_area = 0 # object width * height
		self.yaw_current = 0 # in degrees
		self.depth_current = 0 # in inches
		self.forward_thrust = 0
		self.has_reset = False
		self.timer = 0


	def adjust_yaw(self):
		# rotate yaw until x is within center +/- padding
		new_yaw = Float64() # 0 to 180 degrees (counterclockwise) or -180 degrees (clockwise)
		if self.object_x > CAMERA_WIDTH/2 + CENTER_PADDING_X:
			new_yaw.data = self.yaw_current - YAW_INCREASE
			self.yaw_publisher.publish(new_yaw)
			return False
		elif self.object_x < CAMERA_WIDTH/2 - CENTER_PADDING_X:
			new_yaw.data = self.yaw_current + YAW_INCREASE
			self.yaw_publisher.publish(new_yaw)
			return False
		else:
			return True

	def adjust_depth(self):
		# change depth until y is within center +/- padding
		new_depth = Float64() # 0 to 60 inches
		if self.object_y > CAMERA_HEIGHT/2 + self.yoffset + CENTER_PADDING_Y:
			new_depth.data = self.depth_current + DEPTH_STEP
			self.depth_publisher.publish(new_depth)
			return False
		elif self.object_y < CAMERA_HEIGHT/2 + self.yoffset - CENTER_PADDING_Y:
			new_depth.data = self.depth_current - DEPTH_STEP
			self.depth_publisher.publish(new_depth)
			return False
		else:
			return True

	def adjust_position(self):
		# move forward/backward until object area is within threshold
		if self.object_area/(CAMERA_WIDTH*CAMERA_HEIGHT) < AREA_THRESHOLD_LOW:
			self.change_forward_thrust(FORWARD_THRUST_INCREASE)
			return False
		elif self.object_area/(CAMERA_WIDTH*CAMERA_HEIGHT) > AREA_THRESHOLD_HIGH:
			self.change_forward_thrust(-FORWARD_THRUST_INCREASE)
			return False
		else:
			return True

	def change_forward_thrust(self, amount):
		# only increase/decrease thrust every 200 ticks
		if self.timer % 200 != 0:
			return

		# ensure thrust cannot exceed 280 or -280
		self.forward_thrust = self.forward_thrust + amount
		if self.forward_thrust > MAX_FORWARD_THRUST:
			self.forward_thrust = MAX_FORWARD_THRUST
		elif self.forward_thrust < -MAX_FORWARD_THRUST:
			self.forward_thrust = -MAX_FORWARD_THRUST

		# Publish the new forward thrust
		new_forward_thrust = Int16()
		new_forward_thrust.data = self.forward_thrust
		self.forward_thrust_publisher.publish(new_forward_thrust)

class ChangeDepthState(smach.State):
	def __init__(self, targetDepth, threshold):
		smach.State.__init__(self, outcomes=['done', 'notdone', 'reset'])
		self.targetDepth = targetDepth
		self.threshold = threshold

		self.depth_current = 0 # in inches
		rospy.Subscriber('/depth_control/state', Float64, self.depth_callback)
		self.depth_publisher = rospy.Publisher('/depth_control/setpoint', Float64, queue_size=10)

		self.has_reset = False
		self.reset_subscriber = rospy.Subscriber('/reset', Bool, self.reset_callback)

	def depth_callback(self, msg):
		self.depth_current = msg.data
	def reset_callback(self, msg):
		self.has_reset = msg.data

	def execute(self, userdata):
		if self.has_reset:
			return 'reset'
		if self.depth_current > self.targetDepth + self.threshold:
			self.change_depth(DEPTH_STEP) # submarine is above target, increase depth
			return 'notdone'
		elif self.depth_current < self.targetDepth - self.threshold:
			self.change_depth(-DEPTH_STEP) # submarine is below target, decrease depth
			return 'notdone'
		else:
			return 'done'

	def change_depth(self, amount):
		new_depth = Float64()
		new_depth.data = self.depth + amount
		self.depth_publisher.publish(new_depth)


class RotateYawState(smach.State):
	def __init__(self, targetYaw, threshold):
		smach.State.__init__(self, outcomes=['done', 'notdone', 'reset'])

		self.yaw_target = targetYaw
		self.yaw_variance = threshold

		self.yaw_current = 0 # in degrees
		rospy.Subscriber('/yaw_control/state', Float64, self.yaw_callback) # current orientation
		self.yaw_publisher = rospy.Publisher('yaw_control/setpoint', Float64, queue_size=10) # desired orientation

		self.has_reset = False
		self.reset_subscriber = rospy.Subscriber('/reset', Bool, self.reset_callback)

	def yaw_callback(self, msg):
		self.yaw_current = msg.data
	def reset_callback(self, msg):
		self.has_reset = msg.data

	def execute(self, userdata):
		if self.has_reset:
			self.resetValues()
			return 'reset'

		if self.isYawOnTarget():
			self.resetValues()
			return 'done'
		else:

			return 'notdone'

	def isYawOnTarget(self):
		return False

	def change_yaw(self, direction):
		new_yaw = Float64()
		new_yaw.data = direction * YAW_INCREASE
		self.yaw_publisher.publish(new_yaw)

	def resetValues(self):
		self.yaw_current = 0
		self.has_reset = False

class ResetState(smach.State):
	def __init__(self):
		smach.State.__init__(self, outcomes=['restart', 'stay'])

		# self.has_reset = True
		# rospy.Subscriber('/reset', Bool, self.reset_callback)

	def reset_callback(self, msg):
		self.has_reset = msg.data

	def execute(self, userdata):
		# if self.has_reset:
		# 	return 'stay'
		# else:
		# 	self.has_reset = True
		# 	return 'restart'
		return 'restart'


def main():
	rospy.init_node('torpedo_task_state_machine')
	sm = smach.StateMachine(outcomes=['torpedo_task_complete'])
	sis = smach_ros.IntrospectionServer('server_name', sm, '/SM_ROOT')
	sis.start()

	bouy_flat_topic = {
		'x': '/bouy_flat_x',
		'y': '/bouy_flat_y',
		'area': '/bouy_flat_area'
	}

	bouy_triangle_topic = {
		'x': '/bouy_triangle_x',
		'y': '/bouy_triangle_y',
		'area': '/bouy_triangle_area'
	}

	TOUCH_FLAT_TIMER = 1000
	MOVE_BACK_1_TIMER = 700
	MOVE_FORWARD_TIMER = 2000
	TOUCH_TRIANGLE_TIMER = 1000
	MOVE_BACK_2_TIMER = 1400

	BOUY_ABOVE_DEPTH = 3*12 # 3 feet
	BOUY_CENTER_DEPTH = 6*12 # 6 feet
	TORPEDO_BOARD_CENTER_DEPTH = 5*12 # 5 feet
	DEPTH_VARIANCE = 1 # 1 inch

	YAW_BOUY_BACK = -1.59 # the yaw (in radians) to face the back of the triangle bouy
	YAW_TORPEDO_TASK = 0.5 # the yaw (in radians) to face the torpedo task
	YAW_VARIANCE = 0.1 # in radians

	with sm:
		smach.StateMachine.add('START', StartState(), 
			transitions={'ready':'TRACK_FLAT', 'notready':'START'})
		smach.StateMachine.add('TRACK_FLAT', TrackObjectState(bouy_flat_topic, 0), 
			transitions={'done':'TOUCH_FLAT', 'notdone':'TRACK_FLAT', 'reset':'RESET'})
		smach.StateMachine.add('TOUCH_FLAT', MoveForwardState(TOUCH_FLAT_TIMER, True), 
			transitions={'done':'MOVE_BACK_1', 'notdone':'TOUCH_FLAT', 'reset':'RESET'})
		smach.StateMachine.add('MOVE_BACK_1', MoveForwardState(MOVE_BACK_1_TIMER, False), 
			transitions={'done':'MOVE_UP', 'notdone':'MOVE_BACK_1', 'reset':'RESET'})
		smach.StateMachine.add('MOVE_UP', ChangeDepthState(BOUY_ABOVE_DEPTH, DEPTH_VARIANCE), 
			transitions={'done':'MOVE_FORWARD', 'notdone':'MOVE_UP', 'reset':'RESET'})
		smach.StateMachine.add('MOVE_FORWARD', MoveForwardState(MOVE_FORWARD_TIMER, True), 
			transitions={'done':'MOVE_DOWN', 'notdone':'MOVE_FORWARD', 'reset':'RESET'})
		smach.StateMachine.add('MOVE_DOWN', ChangeDepthState(BOUY_CENTER_DEPTH, DEPTH_VARIANCE), 
			transitions={'done':'TURN_AROUND', 'notdone':'MOVE_DOWN', 'reset':'RESET'})
		smach.StateMachine.add('TURN_AROUND', RotateYawState(YAW_BOUY_BACK, YAW_VARIANCE), 
			transitions={'done':'TRACK_TRIANGLE', 'notdone':'TURN_AROUND', 'reset':'RESET'})
		smach.StateMachine.add('TRACK_TRIANGLE', TrackObjectState(bouy_triangle_topic, 0), 
			transitions={'done':'TOUCH_TRIANGLE', 'notdone':'TRACK_TRIANGLE', 'reset':'RESET'})
		smach.StateMachine.add('TOUCH_TRIANGLE', MoveForwardState(TOUCH_TRIANGLE_TIMER, True), 
			transitions={'done':'MOVE_BACK_2', 'notdone':'TOUCH_TRIANGLE', 'reset':'RESET'})
		smach.StateMachine.add('MOVE_BACK_2', MoveForwardState(MOVE_BACK_2_TIMER, False), 
			transitions={'done':'FACE_TORPEDO_TASK', 'notdone':'MOVE_BACK_2', 'reset':'RESET'})
		smach.StateMachine.add('FACE_TORPEDO_TASK', RotateYawState(YAW_TORPEDO_TASK, YAW_VARIANCE), 
			transitions={'done':'MOVE_TORPEDO_DEPTH', 'notdone':'FACE_TORPEDO_TASK', 'reset':'RESET'})
		smach.StateMachine.add('MOVE_TORPEDO_DEPTH', ChangeDepthState(TORPEDO_BOARD_CENTER_DEPTH, DEPTH_VARIANCE), 
			transitions={'done':'START', 'notdone':'MOVE_TORPEDO_DEPTH', 'reset':'RESET'})
		smach.StateMachine.add('RESET', ResetState(), 
			transitions={'restart':'START', 'stay':'RESET'})

	outcome = sm.execute()
	rospy.spin()
	sis.stop()

if __name__ == '__main__':
	main()