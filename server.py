from flask import Flask, request, jsonify, Response
from pymongo import MongoClient, errors as PyMongoError
from bson import json_util, ObjectId
from urllib.parse import quote_plus
from flask_cors import CORS
import threading
import queue
import time
import datetime
import logging
from gridfs import GridFS

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# ====== Configuration ======
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class MongoDBManager:
    def __init__(self):
        # Get credentials from environment variables
        self.username = os.environ.get("MONGO_USERNAME", "quizapplicationstudents")
        self.password = os.environ.get("MONGO_PASSWORD", "king@App9963")
        self.connect()
        
    def connect(self):
        try:
            encoded_username = quote_plus(self.username)
            encoded_password = quote_plus(self.password)
            MONGO_URI = f"mongodb+srv://{encoded_username}:{encoded_password}@cluster0.dovxs.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
            
            self.client = MongoClient(MONGO_URI,
                serverSelectionTimeoutMS=5000,  # 5 second timeout
                connectTimeoutMS=10000,        # 10 second timeout
                socketTimeoutMS=45000,         # 45 second timeout for operations
                retryWrites=True               # Enable retry for write operations
                )
            self.db = self.client["assignment_submitter"]
            self.users_collection = self.db["users"]
            self.courses_collection = self.db["courses"]
            self.tasks_collection = self.db["tasks"]
            self.events_collection = self.db["events"]  # Add events collection
            
            # Initialize GridFS
            self.fs = GridFS(self.db)
            
            # Verify connection
            self.client.admin.command('ping')
            logger.info("MongoDB connection successful")
            
            # Create indexes
            self.users_collection.create_index([("email", 1)], unique=True)
            self.users_collection.create_index([("user_ID", 1)], unique=True)
            self.courses_collection.create_index([("course_name", 1)])
            self.tasks_collection.create_index([("course_id", 1)])
            self.events_collection.create_index([("date", 1)])  # Index for events by date
            
        except PyMongoError as e:
            logger.error(f"MongoDB connection failed: {e}")
            raise

# Create the MongoDB manager instance
mongo = MongoDBManager()

# ====== Real-Time Event System ======
class EventManager:
    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()
        
    def add_client(self, client_queue):
        with self.lock:
            self.clients.append(client_queue)
            logger.info(f"Added client. Total clients: {len(self.clients)}")
            
    def remove_client(self, client_queue):
        with self.lock:
            if client_queue in self.clients:
                self.clients.remove(client_queue)
                logger.info(f"Removed client. Total clients: {len(self.clients)}")
                
    def broadcast(self, message):
        with self.lock:
            logger.info(f"Broadcasting to {len(self.clients)} clients")
            for client in self.clients[:]:
                try:
                    client.put_nowait(message)
                except queue.Full:
                    logger.warning("Client queue full, removing client")
                    self.clients.remove(client)
                except Exception as e:
                    logger.error(f"Error broadcasting to client: {e}")
                    self.clients.remove(client)

event_manager = EventManager()

def watch_changes():
    """Monitor MongoDB for changes and broadcast events"""
    # Start separate threads for watching each collection
    threading.Thread(target=watch_user_changes, daemon=True).start()
    threading.Thread(target=watch_course_changes, daemon=True).start()
    threading.Thread(target=watch_task_changes, daemon=True).start()
    threading.Thread(target=watch_events_changes, daemon=True).start()  # Add events watcher

def watch_events_changes():
    """Monitor events collection changes and broadcast events"""
    while True:
        try:
            # Only watch for insert, update, delete operations
            pipeline = [{
                '$match': {
                    'operationType': {
                        '$in': ['insert', 'update', 'delete']
                    }
                }
            }]
            
            with mongo.events_collection.watch(
                pipeline=pipeline,
                full_document='updateLookup'
            ) as stream:
                logger.info("Events change stream active, waiting for changes...")
                for change in stream:
                    try:
                        logger.info(f"Events change detected: {change['operationType']}")
                        
                        # Convert ObjectId to string for JSON serialization
                        if 'documentKey' in change and '_id' in change['documentKey']:
                            change['documentKey']['_id'] = str(change['documentKey']['_id'])
                        
                        if 'fullDocument' in change and '_id' in change['fullDocument']:
                            change['fullDocument']['_id'] = str(change['fullDocument']['_id'])
                        
                        # Broadcast the change with event_change type
                        event_manager.broadcast({
                            'type': 'event_change',
                            'operation': change['operationType'],
                            'data': change
                        })
                        
                    except Exception as e:
                        logger.error(f"Error processing events change: {e}")
                        
        except PyMongoError as e:
            logger.error(f"Events change stream error: {e}. Reconnecting in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error in events change stream: {e}. Restarting...")
            time.sleep(5)

def watch_task_changes():
    """Monitor task collection changes and broadcast events"""
    while True:
        try:
            # Only watch for insert, update, delete operations
            pipeline = [{
                '$match': {
                    'operationType': {
                        '$in': ['insert', 'update', 'delete']
                    }
                }
            }]
            
            with mongo.tasks_collection.watch(
                pipeline=pipeline,
                full_document='updateLookup'
            ) as stream:
                logger.info("Task change stream active, waiting for changes...")
                for change in stream:
                    try:
                        logger.info(f"Task change detected: {change['operationType']}")
                        
                        # Convert ObjectId to string for JSON serialization
                        if 'documentKey' in change and '_id' in change['documentKey']:
                            change['documentKey']['_id'] = str(change['documentKey']['_id'])
                        
                        if 'fullDocument' in change and '_id' in change['fullDocument']:
                            change['fullDocument']['_id'] = str(change['fullDocument']['_id'])
                        
                        # Broadcast the change with task_change type
                        event_manager.broadcast({
                            'type': 'task_change',
                            'operation': change['operationType'],
                            'data': change
                        })
                        
                    except Exception as e:
                        logger.error(f"Error processing task change: {e}")
                        
        except PyMongoError as e:
            logger.error(f"Task change stream error: {e}. Reconnecting in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error in task change stream: {e}. Restarting...")
            time.sleep(5)

def watch_user_changes():
    """Monitor user collection changes and broadcast events"""
    while True:
        try:
            # Only watch for insert, update, delete operations
            pipeline = [{
                '$match': {
                    'operationType': {
                        '$in': ['insert', 'update', 'delete']
                    }
                }
            }]
            
            with mongo.users_collection.watch(
                pipeline=pipeline,
                full_document='updateLookup'
            ) as stream:
                logger.info("User change stream active, waiting for changes...")
                for change in stream:
                    try:
                        logger.info(f"User change detected: {change['operationType']}")
                        
                        # Convert ObjectId to string for JSON serialization
                        if 'documentKey' in change and '_id' in change['documentKey']:
                            change['documentKey']['_id'] = str(change['documentKey']['_id'])
                        
                        if 'fullDocument' in change and '_id' in change['fullDocument']:
                            change['fullDocument']['_id'] = str(change['fullDocument']['_id'])
                        
                        # Broadcast the change
                        event_manager.broadcast({
                            'type': 'user_change',
                            'operation': change['operationType'],
                            'data': change
                        })
                        
                    except Exception as e:
                        logger.error(f"Error processing user change: {e}")
                        
        except PyMongoError as e:
            logger.error(f"User change stream error: {e}. Reconnecting in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error in user change stream: {e}. Restarting...")
            time.sleep(5)

def watch_course_changes():
    """Monitor course collection changes and broadcast events"""
    while True:
        try:
            # Only watch for insert, update, delete operations
            pipeline = [{
                '$match': {
                    'operationType': {
                        '$in': ['insert', 'update', 'delete']
                    }
                }
            }]
            
            with mongo.courses_collection.watch(
                pipeline=pipeline,
                full_document='updateLookup'
            ) as stream:
                logger.info("Course change stream active, waiting for changes...")
                for change in stream:
                    try:
                        logger.info(f"Course change detected: {change['operationType']}")
                        
                        # Convert ObjectId to string for JSON serialization
                        if 'documentKey' in change and '_id' in change['documentKey']:
                            change['documentKey']['_id'] = str(change['documentKey']['_id'])
                        
                        if 'fullDocument' in change and '_id' in change['fullDocument']:
                            change['fullDocument']['_id'] = str(change['fullDocument']['_id'])
                        
                        # Broadcast the change with course_change type
                        event_manager.broadcast({
                            'type': 'course_change',
                            'operation': change['operationType'],
                            'data': change
                        })
                        
                    except Exception as e:
                        logger.error(f"Error processing course change: {e}")
                        
        except PyMongoError as e:
            logger.error(f"Course change stream error: {e}. Reconnecting in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error in course change stream: {e}. Restarting...")
            time.sleep(5)

# Start change watcher in background
change_watcher_thread = threading.Thread(target=watch_changes, daemon=True)
change_watcher_thread.start()
logger.info("MongoDB change watcher started")

# ====== SSE Endpoint ======
@app.route('/events')
def sse_events():
    def event_stream():
        q = queue.Queue(maxsize=10)  # Prevent unbounded queue growth
        event_manager.add_client(q)
        logger.info("New SSE client connected")
        
        try:
            while True:
                try:
                    message = q.get(timeout=30)  # Timeout to check client connection
                    yield f"data: {json_util.dumps(message)}\n\n"
                except queue.Empty:
                    # Send keep-alive comment
                    yield ":keep-alive\n\n"
        except GeneratorExit:
            logger.info("Client disconnected normally")
        except Exception as e:
            logger.error(f"SSE stream error: {e}")
        finally:
            event_manager.remove_client(q)
            logger.info("Cleaned up client connection")

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'  # Disable buffering for Nginx
        }
    )

# ====== API Endpoints (with real-time event triggering) ======
@app.route('/user', methods=['POST'])
def add_user():
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['firstname', 'lastname', 'email', 'user_ID', 'role', 'salt', 'OTP', 'created_date', 'created_time', 'password']
        if not data or any(field not in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400
            
        # Check if email exists
        if mongo.users_collection.find_one({'email': data['email']}):
            return jsonify({"error": "Email already exists"}), 409

        # Create user document with status field and empty registered_courses array
        user_doc = {
            'firstname': data['firstname'],
            'lastname': data['lastname'],
            'email': data['email'],
            'user_ID': data['user_ID'],
            'role': data['role'],
            'salt': data['salt'],
            'OTP': data['OTP'],
            'is_online': data.get('is_online', False),
            'created_date': data['created_date'],
            'created_time': data['created_time'],
            'password': data['password'],
            'profile_image': data.get('profile_image', ''),
            'otp_expires': datetime.datetime.now() + datetime.timedelta(minutes=5),
            'approved': data.get('approved', False),  # Default approved status is False if not provided
            'registered_courses': data.get('registered_courses', [])  # Initialize as empty array if not provided
        }

        result = mongo.users_collection.insert_one(user_doc)
        
        # No need to manually broadcast - change stream will pick this up
        return jsonify({
            "message": "User added successfully",
            "id": str(result.inserted_id)
        }), 201
        
    except Exception as e:
        logger.error(f"Error adding user: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/user/update/<user_id>', methods=['PUT'])
def update_user(user_id):
    try:
        # Validate ObjectId format
        if not ObjectId.is_valid(user_id):
            return jsonify({"error": "Invalid user ID format"}), 400
            
        data = request.get_json()
        if not data:
            return jsonify({"error": "No update data provided"}), 400
            
        # Prepare update fields - only allow specific fields to be updated
        update_fields = {}
        allowed_fields = ['firstname', 'lastname', 'email', 'role', 'profile_image']
        
        for field in allowed_fields:
            if field in data:
                update_fields[field] = data[field]
                
        if not update_fields:
            return jsonify({"error": "No valid fields to update"}), 400
            
        # Perform the update
        result = mongo.users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': update_fields}
        )
        
        if result.matched_count == 0:
            return jsonify({"error": "User not found"}), 404
            
        # Get the updated user document
        user = mongo.users_collection.find_one(
            {'_id': ObjectId(user_id)},
            {'password': 0, 'salt': 0, 'OTP': 0}  # Exclude sensitive fields
        )
        
        # Return response with json_util to handle ObjectId
        return Response(
            json_util.dumps({
                "message": "User updated successfully",
                "user": user
            }),
            mimetype='application/json'
        ), 200
        
    except Exception as e:
        logger.error(f"Error updating user: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/users', methods=['GET'])
def get_users():
    try:
        users = list(mongo.users_collection.find({}, {'password': 0, 'salt': 0, 'OTP': 0}))
        return Response(
            json_util.dumps(users),
            mimetype='application/json'
        )
    except Exception as e:
        logger.error(f"Error getting users: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/user/<user_ID>', methods=['GET'])
def get_user(user_ID):
    try:
        user = mongo.users_collection.find_one(
            {'user_ID': user_ID},
            # {'password': 0}
        )
        if not user:
            return jsonify({"error": "User not found"}), 404
            
        user['_id'] = str(user['_id'])
        return jsonify(user)
    except Exception as e:
        logger.error(f"Error getting user: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/user/byid/<user_id>', methods=['GET'])
def get_user_by_id(user_id):
    try:
        # Validate ObjectId format
        if not ObjectId.is_valid(user_id):
            return jsonify({"error": "Invalid user ID format"}), 400
            
        user = mongo.users_collection.find_one(
            {'_id': ObjectId(user_id)},
            {'password': 0, 'salt': 0, 'OTP': 0}  # Exclude sensitive fields
        )
        
        if not user:
            return jsonify({"error": "User not found"}), 404
            
        return Response(
            json_util.dumps(user),
            mimetype='application/json'
        )
    except Exception as e:
        logger.error(f"Error getting user by ID: {e}")
        return jsonify({"error": str(e)}), 500
    

@app.route('/user/password/<user_ID>', methods=['PUT'])
def update_password(user_ID):
    try:
        data = request.get_json()
        if not data or 'password' not in data or 'salt' not in data:
            return jsonify({"error": "Password and salt are required"}), 400
            
        result = mongo.users_collection.update_one(
            {'user_ID': user_ID},
            {'$set': {
                'password': data['password'],
                'salt': data['salt']
            }}
        )
        
        if result.modified_count == 0:
            return jsonify({"error": "User not found"}), 404
            
        return jsonify({"message": "Password updated successfully"})
    except Exception as e:
        logger.error(f"Error updating password: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/user/<state>/<user_id>', methods=['PUT'])
def update_approval(state, user_id):
    try:
        data = request.get_json()
        if not data or 'approved' not in data:
            return jsonify({"error": "Approved status is required"}), 400
            
        # Ensure approved is stored as a boolean
        approved_status = bool(data['approved'])
            
        result = mongo.users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {'approved': approved_status}}
        )
        
        if result.matched_count == 0:
            return jsonify({"error": "User not found"}), 404
            
        return jsonify({
            "message": "Approval status updated successfully", 
            "_id": user_id,
            "approved": approved_status
        })
    except Exception as e:
        logger.error(f"Error updating approval status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/user/online-status/<user_id>', methods=['PUT'])
def update_online_status(user_id):
    try:
        # Validate ObjectId format
        if not ObjectId.is_valid(user_id):
            return jsonify({"error": "Invalid user ID format"}), 400
            
        data = request.get_json()
        if not data or 'is_online' not in data:
            return jsonify({"error": "Online status is required"}), 400
            
        # Ensure is_online is stored as a boolean
        is_online = bool(data['is_online'])
            
        result = mongo.users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {'is_online': is_online}}
        )
        
        if result.matched_count == 0:
            return jsonify({"error": "User not found"}), 404
            
        return jsonify({
            "message": "Online status updated successfully", 
            "_id": user_id,
            "is_online": is_online
        })
    except Exception as e:
        logger.error(f"Error updating online status: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/verify-otp', methods=['POST'])
def verify_otp():
    try:
        data = request.get_json()
        if not data or 'user_ID' not in data or 'otp' not in data:
            return jsonify({"error": "user_ID and OTP are required"}), 400
            
        user = mongo.users_collection.find_one({
            'user_ID': data['user_ID'],
            'OTP': data['otp']
        })
        
        if not user:
            return jsonify({"error": "Invalid OTP"}), 401
            
        if datetime.datetime.now() > user['otp_expires']:
            return jsonify({"error": "OTP expired"}), 401
            
        return jsonify({"message": "Verification successful"}), 200
    except Exception as e:
        logger.error(f"Error verifying OTP: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/user/otp/<user_ID>', methods=['PUT'])
def update_OTP(user_ID):
    try:
        data = request.get_json()
        if not data or 'OTP' not in data:
            return jsonify({"error": "OTP is required"}), 400
            
        result = mongo.users_collection.update_one(
            {'user_ID': user_ID},
            {'$set': {
                'OTP': data['OTP'],
                'otp_expires': datetime.datetime.now() + datetime.timedelta(minutes=1)
            }}
        )
        
        if result.modified_count == 0:
            return jsonify({"error": "User not found"}), 404
            
        return jsonify({"message": "OTP updated successfully"})
    except Exception as e:
        logger.error(f"Error updating OTP: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/users/<user_id>', methods=['DELETE'])
def delete_user(user_id):
    try:
        # Validate the ObjectId format
        if not ObjectId.is_valid(user_id):
            return jsonify({"error": "Invalid user ID format"}), 400

        # Perform the deletion
        result = mongo.db.users.delete_one({'_id': ObjectId(user_id)})
        
        if result.deleted_count == 1:
            # Return the deleted user's data
            return jsonify({
                "message": "User deleted successfully",
                "deleted_id": user_id
            }), 200
        else:
            return jsonify({"error": "User not found"}), 404
            
    except Exception as e:
        logger.error(f"Error deleting user: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500
    

@app.route('/users/delete-many', methods=['POST'])
def delete_many_users():
    try:
        data = request.get_json()
        
        if not data or 'ids' not in data or not isinstance(data['ids'], list):
            return jsonify({"error": "Request must include 'ids' list"}), 400
        
        # Convert string IDs to ObjectId objects
        object_ids = []
        invalid_ids = []
        
        for id_str in data['ids']:
            try:
                object_ids.append(ObjectId(id_str))
            except:
                invalid_ids.append(id_str)
        
        # If we have valid IDs, delete them
        deleted_count = 0
        if object_ids:
            result = mongo.users_collection.delete_many({"_id": {"$in": object_ids}})
            deleted_count = result.deleted_count
        
        return jsonify({
            "message": f"Successfully deleted {deleted_count} users",
            "deleted_count": deleted_count,
            "invalid_ids": invalid_ids
        }), 200
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    

# ==================================== COURSES ENDPOINTS ====================================================

@app.route('/courses', methods=['GET'])
def get_courses():
    try:
        courses = list(mongo.courses_collection.find({}))
        return Response(
            json_util.dumps(courses),
            mimetype='application/json'
        )
    except Exception as e:
        logger.error(f"Error getting courses: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/course/<course_id>', methods=['PUT'])
def update_course(course_id):
    try:
        # Validate ObjectId format
        if not ObjectId.is_valid(course_id):
            return jsonify({"error": "Invalid course ID format"}), 400
            
        data = request.get_json()
        if not data:
            return jsonify({"error": "No update data provided"}), 400
            
        # Prepare update fields
        update_fields = {}
        
        # Only allow specific fields to be updated
        if 'course_name' in data:
            update_fields['course_name'] = data['course_name']
        if 'instructor_id' in data:
            update_fields['instructor_id'] = data['instructor_id']
        if 'student_id' in data:
            update_fields['student_id'] = data['student_id']
            
        if not update_fields:
            return jsonify({"error": "No valid fields to update"}), 400

        # Verify course exists
        course = mongo.courses_collection.find_one({'_id': ObjectId(course_id)})
        if not course:
            return jsonify({"error": "Course not found"}), 404
            
        # Perform the update
        result = mongo.courses_collection.update_one(
            {'_id': ObjectId(course_id)},
            {'$set': update_fields}
        )
        
        if result.modified_count == 0:
            return jsonify({"message": "No changes were made"}), 200
            
        return jsonify({
            "message": "Course updated successfully",
            "modified_count": result.modified_count
        }), 200
        
    except Exception as e:
        logger.error(f"Error updating course: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/course/<course_id>', methods=['DELETE'])
def delete_course(course_id):
    try:
        # Validate ObjectId format
        if not ObjectId.is_valid(course_id):
            return jsonify({"error": "Invalid course ID format"}), 400

        # Verify course exists
        course = mongo.courses_collection.find_one({'_id': ObjectId(course_id)})
        if not course:
            return jsonify({"error": "Course not found"}), 404
            
        # Perform the deletion
        result = mongo.courses_collection.delete_one({'_id': ObjectId(course_id)})
        
        if result.deleted_count == 1:
            return jsonify({
                "message": "Course deleted successfully",
                "deleted_id": course_id
            }), 200
        else:
            return jsonify({"error": "Failed to delete course"}), 500
            
    except Exception as e:
        logger.error(f"Error deleting course: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/course/enroll', methods=['POST'])
def enroll_student():
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data or 'course_id' not in data or 'student_id' not in data:
            return jsonify({"error": "Course ID and student ID are required"}), 400
            
        course_id = data['course_id']
        student_id = data['student_id']
        
        # Validate ObjectId format
        if not ObjectId.is_valid(course_id):
            return jsonify({"error": "Invalid course ID format"}), 400
            
        # Verify course exists
        course = mongo.courses_collection.find_one({'_id': ObjectId(course_id)})
        if not course:
            return jsonify({"error": "Course not found"}), 404
            
        # Verify student exists
        student = mongo.users_collection.find_one({'_id': ObjectId(student_id)})
        if not student:
            return jsonify({"error": "Student not found"}), 404
            
        # Check if student is already enrolled
        if student_id in course.get('student_id', []):
            return jsonify({"error": "Student already enrolled in this course"}), 409
            
        # Add student to course
        result = mongo.courses_collection.update_one(
            {'_id': ObjectId(course_id)},
            {'$push': {'student_id': student_id}}
        )
        
        if result.modified_count == 1:
            return jsonify({
                "message": "Student enrolled successfully"
            }), 200
        else:
            return jsonify({"error": "Failed to enroll student"}), 500
            
    except Exception as e:
        logger.error(f"Error enrolling student: {e}")
        return jsonify({"error": str(e)}), 500
        
    except Exception as e:
        logger.error(f"Error creating course: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/course', methods=['POST'])
def create_course():
    try:
        data = request.get_json()
        # Validate required fields
        required_fields = ['course_name', 'instructor_id', 'date_created', 'time_created']
        if not data or any(field not in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400
        
        # Check if course already exists with the same name
        existing_course = mongo.courses_collection.find_one({'course_name': data['course_name']})
        if existing_course:
            return jsonify({"error": "A course with this name already exists"}), 409
            
        # Create course document
        course_doc = {
            'course_name': data['course_name'],
            'student_id': [],  # Empty array for students as specified
            'instructor_id': data['instructor_id'],
            'date_created': data['date_created'],
            'time_created': data['time_created']
        }
        
        # Add instructor_name if provided
        if 'instructor_name' in data:
            course_doc['instructor_name'] = data['instructor_name']

        # Verify instructor exists
        instructor = mongo.users_collection.find_one({'_id': ObjectId(data['instructor_id'])})
        if not instructor:
            return jsonify({"error": "Instructor not found"}), 404
            
        result = mongo.courses_collection.insert_one(course_doc)
        
        # No need to manually broadcast - change stream will pick this up
        return jsonify({
            "message": "Course created successfully",
            "id": str(result.inserted_id)
        }), 201
        
    except Exception as e:
        logger.error(f"Error creating course: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/user/register-courses', methods=['POST'])
def register_courses():
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data or 'user_id' not in data or 'courses' not in data:
            return jsonify({"error": "User ID and courses are required"}), 400
            
        user_id = data['user_id']
        course_ids = data['courses']
        
        # Validate user ID format
        if not ObjectId.is_valid(user_id):
            return jsonify({"error": "Invalid user ID format"}), 400
            
        # Verify user exists
        user = mongo.users_collection.find_one({'_id': ObjectId(user_id)})
        if not user:
            return jsonify({"error": "User not found"}), 404
            
        # Validate course IDs format and existence
        valid_course_ids = []
        invalid_course_ids = []
        
        for course_id in course_ids:
            if not ObjectId.is_valid(course_id):
                invalid_course_ids.append({"id": course_id, "reason": "Invalid format"})
                continue
                
            course = mongo.courses_collection.find_one({'_id': ObjectId(course_id)})
            if not course:
                invalid_course_ids.append({"id": course_id, "reason": "Course not found"})
                continue
                
            valid_course_ids.append(course_id)
        
        # If there are no valid course IDs, return error
        if not valid_course_ids:
            return jsonify({
                "error": "No valid courses to register",
                "invalid_courses": invalid_course_ids
            }), 400
            
        # Update user's registered_courses
        result = mongo.users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {'$addToSet': {'registered_courses': {'$each': valid_course_ids}}}
        )
        
        # Also add student to the courses' student_id arrays
        for course_id in valid_course_ids:
            mongo.courses_collection.update_one(
                {'_id': ObjectId(course_id)},
                {'$addToSet': {'student_id': user_id}}
            )
        
        return jsonify({
            "message": "Courses registered successfully",
            "registered_courses": valid_course_ids,
            "invalid_courses": invalid_course_ids
        }), 200
        
    except Exception as e:
        logger.error(f"Error registering courses: {e}")
        return jsonify({"error": str(e)}), 500

# Add endpoint to get user's registered courses with details
@app.route('/user/registered-courses/<user_id>', methods=['GET'])
def get_registered_courses(user_id):
    try:
        # Validate user ID format
        if not ObjectId.is_valid(user_id):
            return jsonify({"error": "Invalid user ID format"}), 400
            
        # Verify user exists
        user = mongo.users_collection.find_one({'_id': ObjectId(user_id)})
        if not user:
            return jsonify({"error": "User not found"}), 404
            
        # Get registered course IDs
        registered_course_ids = user.get('registered_courses', [])
        
        # If no registered courses, return empty array
        if not registered_course_ids:
            return jsonify({"registered_courses": []}), 200
            
        # Convert string IDs to ObjectId if needed
        object_ids = []
        for course_id in registered_course_ids:
            if isinstance(course_id, str) and ObjectId.is_valid(course_id):
                object_ids.append(ObjectId(course_id))
            elif isinstance(course_id, ObjectId):
                object_ids.append(course_id)
        
        # Fetch course details
        courses = list(mongo.courses_collection.find({'_id': {'$in': object_ids}}))
        
        return Response(
            json_util.dumps(courses),
            mimetype='application/json'
        )
        
    except Exception as e:
        logger.error(f"Error getting registered courses: {e}")
        return jsonify({"error": str(e)}), 500

# Add endpoint to unregister from courses
@app.route('/user/unregister-courses', methods=['POST'])
def unregister_courses():
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data or 'user_id' not in data or 'courses' not in data:
            return jsonify({"error": "User ID and courses are required"}), 400
            
        user_id = data['user_id']
        course_ids = data['courses']
        
        # Validate user ID format
        if not ObjectId.is_valid(user_id):
            return jsonify({"error": "Invalid user ID format"}), 400
            
        # Verify user exists
        user = mongo.users_collection.find_one({'_id': ObjectId(user_id)})
        if not user:
            return jsonify({"error": "User not found"}), 404
            
        # Validate course IDs format
        valid_course_ids = []
        invalid_course_ids = []
        
        for course_id in course_ids:
            if not ObjectId.is_valid(course_id):
                invalid_course_ids.append({"id": course_id, "reason": "Invalid format"})
                continue
            valid_course_ids.append(course_id)
        
        # If there are no valid course IDs, return error
        if not valid_course_ids:
            return jsonify({
                "error": "No valid courses to unregister",
                "invalid_courses": invalid_course_ids
            }), 400
            
        # Update user's registered_courses
        result = mongo.users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {'$pullAll': {'registered_courses': valid_course_ids}}
        )
        
        # Also remove student from the courses' student_id arrays
        for course_id in valid_course_ids:
            mongo.courses_collection.update_one(
                {'_id': ObjectId(course_id)},
                {'$pull': {'student_id': user_id}}
            )
        
        return jsonify({
            "message": "Courses unregistered successfully",
            "unregistered_courses": valid_course_ids,
            "invalid_courses": invalid_course_ids
        }), 200
        
    except Exception as e:
        logger.error(f"Error unregistering courses: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/courses/instructor/<instructor_id>', methods=['GET'])
def get_instructor_courses(instructor_id):
    try:
        # Validate instructor_id format
        if not ObjectId.is_valid(instructor_id):
            return jsonify({"error": "Invalid instructor ID format"}), 400
            
        # Get all courses for the specified instructor
        courses = list(mongo.courses_collection.find({'instructor_id': instructor_id}))
        
        return Response(
            json_util.dumps(courses),
            mimetype='application/json'
        )
    except Exception as e:
        logger.error(f"Error getting instructor courses: {e}")
        return jsonify({"error": str(e)}), 500

# ==================================== TASKS ENDPOINTS ====================================================

@app.route('/task', methods=['POST'])
def create_task():
    try:
        # Check if the request is multipart (has files)
        if request.files:
            # Get form data
            course_id = request.form.get('course_id')
            instructor_id = request.form.get('instructor_id')
            comment = request.form.get('comment', '')
            score = request.form.get('score', 0)
            is_published = request.form.get('is_published') == 'true'
            
            # Validate required fields
            if not all([course_id, instructor_id]):
                return jsonify({"error": "Missing required fields"}), 400
        else:
            # JSON data for non-file requests
            data = request.get_json()
            course_id = data.get('course_id')
            instructor_id = data.get('instructor_id')
            comment = data.get('comment', '')
            score = data.get('score', 0)
            is_published = bool(data.get('is_published'))
            
            if not all([course_id, instructor_id]):
                return jsonify({"error": "Missing required fields"}), 400
        
        # Validate course_id and instructor_id format
        if not ObjectId.is_valid(course_id):
            return jsonify({"error": "Invalid course ID format"}), 400
            
        if not ObjectId.is_valid(instructor_id):
            return jsonify({"error": "Invalid instructor ID format"}), 400
            
        # Verify course exists
        course = mongo.courses_collection.find_one({'_id': ObjectId(course_id)})
        if not course:
            return jsonify({"error": "Course not found"}), 404
            
        # Verify instructor exists
        instructor = mongo.users_collection.find_one({'_id': ObjectId(instructor_id)})
        if not instructor:
            return jsonify({"error": "Instructor not found"}), 404
        
        # Verify instructor is associated with the course
        if instructor_id != course.get('instructor_id'):
            return jsonify({"error": "Instructor is not associated with this course"}), 403
        
        # Handle file uploads if present
        task_files = []
        if request.files:
            files = request.files.getlist('files')
            for file in files:
                if file.filename:
                    # Validate file type (PDF only)
                    if not file.filename.lower().endswith('.pdf'):
                        return jsonify({"error": "Only PDF files are allowed"}), 400
                    
                    # Read the file binary data
                    file_data = file.read()
                    
                    # Store the file in GridFS
                    file_id = mongo.fs.put(
                        file_data, 
                        filename=file.filename,
                        content_type=file.content_type,
                        metadata={
                            'original_filename': file.filename,
                            'content_type': file.content_type,
                            'size': len(file_data)
                        }
                    )
                    
                    # Add file info to task_files
                    task_files.append({
                        'file_id': str(file_id),
                        'filename': file.filename,
                        'content_type': file.content_type,
                        'size': len(file_data)
                    })
        elif 'task_files' in data:
            # Handle task_files from JSON data (legacy support)
            # Note: This would require pre-uploaded files or file IDs
            task_files = data.get('task_files', [])
        
        # Create task document
        task_doc = {
            'course_id': course_id,
            'instructor_id': instructor_id,
            'comment': comment,
            'task_files': task_files,
            'score': int(score),
            'is_published': is_published,
            'created_date': datetime.datetime.now().strftime('%Y-%m-%d'),
            'created_time': datetime.datetime.now().strftime('%H:%M:%S'),
            'submissions': []  # Initialize empty array for student submissions
        }
        
        # Add instructor_name if available
        if instructor.get('firstname') and instructor.get('lastname'):
            task_doc['instructor_name'] = f"{instructor['firstname']} {instructor['lastname']}"
        
        # Add course_name if available
        if course.get('course_name'):
            task_doc['course_name'] = course['course_name']
        
        result = mongo.tasks_collection.insert_one(task_doc)
        
        return jsonify({
            "message": "Task created successfully",
            "id": str(result.inserted_id),
            "files_uploaded": len(task_files)
        }), 201
        
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        return jsonify({"error": str(e)}), 500
    

@app.route('/tasks', methods=['GET'])
def get_tasks():
    try:
        tasks = list(mongo.tasks_collection.find({}))
        return Response(
            json_util.dumps(tasks),
            mimetype='application/json'
        )
    except Exception as e:
        logger.error(f"Error getting tasks: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/tasks/course/<course_id>', methods=['GET'])
def get_tasks_by_course(course_id):
    try:
        # Validate course_id format
        if not ObjectId.is_valid(course_id):
            return jsonify({"error": "Invalid course ID format"}), 400
            
        # Get all tasks for the specified course
        tasks = list(mongo.tasks_collection.find({'course_id': course_id}))
        
        return Response(
            json_util.dumps(tasks),
            mimetype='application/json'
        )
    except Exception as e:
        logger.error(f"Error getting tasks for course: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/task/<task_id>', methods=['GET'])
def get_task(task_id):
    try:
        # Validate task_id format
        if not ObjectId.is_valid(task_id):
            return jsonify({"error": "Invalid task ID format"}), 400
            
        # Get the task
        task = mongo.tasks_collection.find_one({'_id': ObjectId(task_id)})
        
        if not task:
            return jsonify({"error": "Task not found"}), 404
            
        return Response(
            json_util.dumps(task),
            mimetype='application/json'
        )
    except Exception as e:
        logger.error(f"Error getting task: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/task/<task_id>', methods=['PUT'])
def update_task(task_id):
    try:
        # Validate task_id format
        if not ObjectId.is_valid(task_id):
            return jsonify({"error": "Invalid task ID format"}), 400
            
        data = request.get_json()
        if not data:
            return jsonify({"error": "No update data provided"}), 400
            
        # Get the existing task
        task = mongo.tasks_collection.find_one({'_id': ObjectId(task_id)})
        if not task:
            return jsonify({"error": "Task not found"}), 404
            
        # Prepare update fields
        update_fields = {}
        
        # Fields that can be updated
        if 'comment' in data:
            update_fields['comment'] = data['comment']
        if 'task_files' in data:
            update_fields['task_files'] = data['task_files']
        if 'score' in data:
            update_fields['score'] = data['score']
        if 'is_published' in data:
            update_fields['is_published'] = bool(data['is_published'])
            
        if not update_fields:
            return jsonify({"error": "No valid fields to update"}), 400
            
        # Perform the update
        result = mongo.tasks_collection.update_one(
            {'_id': ObjectId(task_id)},
            {'$set': update_fields}
        )
        
        if result.modified_count == 0:
            return jsonify({"message": "No changes were made"}), 200
            
        return jsonify({
            "message": "Task updated successfully",
            "modified_count": result.modified_count
        }), 200
        
    except Exception as e:
        logger.error(f"Error updating task: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/task/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    try:
        # Validate task_id format
        if not ObjectId.is_valid(task_id):
            return jsonify({"error": "Invalid task ID format"}), 400
            
        # Verify task exists
        task = mongo.tasks_collection.find_one({'_id': ObjectId(task_id)})
        if not task:
            return jsonify({"error": "Task not found"}), 404
            
        # Perform the deletion
        result = mongo.tasks_collection.delete_one({'_id': ObjectId(task_id)})
        
        if result.deleted_count == 1:
            return jsonify({
                "message": "Task deleted successfully",
                "deleted_id": task_id
            }), 200
        else:
            return jsonify({"error": "Failed to delete task"}), 500
            
    except Exception as e:
        logger.error(f"Error deleting task: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/task/<task_id>/file/<file_id>', methods=['GET'])
def get_task_file(task_id, file_id):
    try:
        # Validate IDs
        if not ObjectId.is_valid(task_id) or not ObjectId.is_valid(file_id):
            return jsonify({"error": "Invalid ID format"}), 400
            
        # Check if task exists
        task = mongo.tasks_collection.find_one({'_id': ObjectId(task_id)})
        if not task:
            return jsonify({"error": "Task not found"}), 404
            
        # Check if file_id is in task's files
        file_found = False
        
        # First, check in task_files
        task_files = task.get('task_files', [])
        for file in task_files:
            if str(file.get('file_id')) == str(file_id):
                file_found = True
                break
        
        # If not found in task_files, check in submission_files within submissions
        if not file_found:
            submissions = task.get('submissions', [])
            for submission in submissions:
                submission_files = submission.get('submission_files', [])
                for sub_file in submission_files:
                    if str(sub_file.get('file_id')) == str(file_id):
                        file_found = True
                        break
                if file_found:
                    break
        
        if not file_found:
            return jsonify({"error": "File not associated with this task or its submissions"}), 404
        
        # Retrieve file from GridFS
        try:
            grid_fs_file = mongo.fs.get(ObjectId(file_id))
        except:
            return jsonify({"error": "File not found in storage"}), 404
            
        # Return the file with proper content type
        return Response(
            grid_fs_file.read(),
            mimetype=grid_fs_file.metadata.get('content_type', 'application/pdf'),
            headers={
                'Content-Disposition': f'inline; filename="{grid_fs_file.metadata.get("original_filename", "document.pdf")}"'
            }
        )
        
    except Exception as e:
        logger.error(f"Error fetching task file: {e}")
        return jsonify({"error": str(e)}), 500

# Add endpoint for student task submission
@app.route('/task/submit/<task_id>', methods=['POST'])
def submit_task(task_id):
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data or 'student_id' not in data or 'submission_files' not in data:
            return jsonify({"error": "Missing required fields"}), 400
            
        # Validate IDs format
        if not ObjectId.is_valid(task_id):
            return jsonify({"error": "Invalid task ID format"}), 400
            
        if not ObjectId.is_valid(data['student_id']):
            return jsonify({"error": "Invalid student ID format"}), 400
            
        # Verify task exists
        task = mongo.tasks_collection.find_one({'_id': ObjectId(task_id)})
        if not task:
            return jsonify({"error": "Task not found"}), 404
            
        # Verify student exists
        student = mongo.users_collection.find_one({'_id': ObjectId(data['student_id'])})
        if not student:
            return jsonify({"error": "Student not found"}), 404
            
        # Verify task is published
        if not task.get('is_published', False):
            return jsonify({"error": "Cannot submit to an unpublished task"}), 403
            
        # Create submission object
        submission = {
            'student_id': data['student_id'],
            'student_name': f"{student.get('firstname', '')} {student.get('lastname', '')}",
            'submission_files': data['submission_files'],
            'comment': data.get('comment', ''),
            'submitted_date': datetime.datetime.now().strftime('%Y-%m-%d'),
            'submitted_time': datetime.datetime.now().strftime('%H:%M:%S'),
            'grade': None,  # Initial grade is null
            'feedback': ''   # Initial feedback is empty
        }
        
        # Check if student has already submitted
        existing_submission = next(
            (s for s in task.get('submissions', []) if s.get('student_id') == data['student_id']), 
            None
        )
        
        if existing_submission:
            # Update existing submission
            result = mongo.tasks_collection.update_one(
                {
                    '_id': ObjectId(task_id),
                    'submissions.student_id': data['student_id']
                },
                {
                    '$set': {
                        'submissions.$': submission
                    }
                }
            )
            submission_message = "Task submission updated successfully"
        else:
            # Add new submission
            result = mongo.tasks_collection.update_one(
                {'_id': ObjectId(task_id)},
                {'$push': {'submissions': submission}}
            )
            submission_message = "Task submitted successfully"
        
        if result.modified_count == 0:
            return jsonify({"error": "Failed to submit task"}), 500
            
        return jsonify({
            "message": submission_message,
            "task_id": task_id,
            "student_id": data['student_id']
        }), 200
        
    except Exception as e:
        logger.error(f"Error submitting task: {e}")
        return jsonify({"error": str(e)}), 500

# Get all tasks for a student
@app.route('/tasks/student/<student_id>', methods=['GET'])
def get_student_tasks(student_id):
    try:
        # Validate student_id format
        if not ObjectId.is_valid(student_id):
            return jsonify({"error": "Invalid student ID format"}), 400
            
        # Verify student exists
        student = mongo.users_collection.find_one({'_id': ObjectId(student_id)})
        if not student:
            return jsonify({"error": "Student not found"}), 404
            
        # Get student's registered courses
        registered_courses = student.get('registered_courses', [])
        
        # Convert string IDs to ObjectId if needed
        course_object_ids = []
        for course_id in registered_courses:
            if isinstance(course_id, str) and ObjectId.is_valid(course_id):
                course_object_ids.append(course_id)
            elif isinstance(course_id, ObjectId):
                course_object_ids.append(str(course_id))
                
        # Get published tasks for the student's courses
        tasks = list(mongo.tasks_collection.find({
            'course_id': {'$in': registered_courses},
            'is_published': True
        }))
        
        # For each task, check if the student has submitted and add details
        for task in tasks:
            # Get course name for the task
            course_id = task.get('course_id')
            course = mongo.courses_collection.find_one({'_id': ObjectId(course_id)}) if ObjectId.is_valid(course_id) else None
            task['course_name'] = course.get('course_name', 'Unknown Course') if course else 'Unknown Course'
            
            # Check if student has submitted
            submission = next(
                (s for s in task.get('submissions', []) if s.get('student_id') == student_id),
                None
            )
            task['has_submitted'] = submission is not None
            if submission:
                task['submission_date'] = f"{submission.get('submitted_date', 'N/A')} {submission.get('submitted_time', '')}"
                task['submission'] = submission
            else:
                task['submission_date'] = 'Not submitted'
        
        return Response(
            json_util.dumps(tasks),
            mimetype='application/json'
        )
        
    except Exception as e:
        logger.error(f"Error getting student tasks: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/task/student/<task_id>/<student_id>', methods=['GET'])
def get_student_task_submission(task_id, student_id):
    try:
        # Validate IDs format
        if not ObjectId.is_valid(task_id):
            return jsonify({"error": "Invalid task ID format"}), 400
            
        if not ObjectId.is_valid(student_id):
            return jsonify({"error": "Invalid student ID format"}), 400
            
        # Get the task
        task = mongo.tasks_collection.find_one({'_id': ObjectId(task_id)})
        if not task:
            return jsonify({"error": "Task not found"}), 404
            
        # Verify student exists
        student = mongo.users_collection.find_one({'_id': ObjectId(student_id)})
        if not student:
            return jsonify({"error": "Student not found"}), 404
            
        # Find the submission for this student
        submission = next(
            (s for s in task.get('submissions', []) if s.get('student_id') == student_id),
            None
        )
        
        if not submission:
            return jsonify({"error": "Submission not found"}), 404
            
        # Add course information
        course_id = task.get('course_id')
        course = mongo.courses_collection.find_one({'_id': ObjectId(course_id)})
        if course:
            task['course_name'] = course.get('course_name', 'Unknown Course')
            
        # Prepare response
        return Response(
            json_util.dumps({
                'task': task,
                'submission': submission
            }),
            mimetype='application/json'
        )
        
    except Exception as e:
        logger.error(f"Error getting student task submission: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/task/student/submit/<task_id>', methods=['POST'])
def student_submit_task(task_id):
    try:
        # Check if the request is multipart (has files)
        if request.files:
            # Get form data
            student_id = request.form.get('student_id')
            comment = request.form.get('comment', '')
            
            # Validate required fields
            if not student_id:
                return jsonify({"error": "Student ID is required"}), 400
        else:
            # JSON data for non-file requests (not recommended for file uploads)
            return jsonify({"error": "Files must be uploaded as multipart/form-data"}), 400
            
        # Validate IDs format
        if not ObjectId.is_valid(task_id):
            return jsonify({"error": "Invalid task ID format"}), 400
            
        if not ObjectId.is_valid(student_id):
            return jsonify({"error": "Invalid student ID format"}), 400
            
        # Verify task exists
        task = mongo.tasks_collection.find_one({'_id': ObjectId(task_id)})
        if not task:
            return jsonify({"error": "Task not found"}), 404
            
        # Verify student exists
        student = mongo.users_collection.find_one({'_id': ObjectId(student_id)})
        if not student:
            return jsonify({"error": "Student not found"}), 404
            
        # Verify task is published
        if not task.get('is_published', False):
            return jsonify({"error": "Cannot submit to an unpublished task"}), 403
            
        # Verify student is registered for the course
        course_id = task.get('course_id')
        course_name = task.get('course_name', '')
        if course_id not in student.get('registered_courses', []):
            return jsonify({"error": "You are not registered for this course"}), 403
        
        # Check if submission is allowed based on events
        today = datetime.datetime.now().strftime('%Y-%m-%d')
        
        # Find events for today for this course
        query = {
            f"event.{today}": {"$exists": True},
            "course_name": course_name
        }
        
        events = list(mongo.events_collection.find(query))
        
        # Check if any events have std_can_update_task set to False
        for event_doc in events:
            today_events = event_doc.get("event", {}).get(today, [])
            
            for event in today_events:
                # Check if this event has std_can_update_task=False
                if len(event) >= 8 and event[7] is False:
                    return jsonify({
                        "error": "Submission time has passed for this assignment",
                        "blocking_event": {
                            "time": event[1],
                            "description": event[2]
                        }
                    }), 403

        # Handle file uploads
        submission_files = []
        files = request.files.getlist('files')
        
        if not files or all(not file.filename for file in files):
            return jsonify({"error": "No files uploaded"}), 400
            
        for file in files:
            if file.filename:
                # Validate file type (PDF only)
                if not file.filename.lower().endswith('.pdf'):
                    return jsonify({"error": "Only PDF files are allowed"}), 400
                
                # Read the file binary data
                file_data = file.read()
                
                # Store the file in GridFS
                file_id = mongo.fs.put(
                    file_data, 
                    filename=file.filename,
                    content_type=file.content_type,
                    metadata={
                        'original_filename': file.filename,
                        'content_type': file.content_type,
                        'size': len(file_data),
                        'task_id': task_id,
                        'student_id': student_id,
                        'submission_type': 'student_submission',
                        'date_uploaded': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                )
                
                # Add file info to submission_files
                submission_files.append({
                    'file_id': str(file_id),
                    'filename': file.filename,
                    'content_type': file.content_type,
                    'size': len(file_data)
                })
        
        # Create submission object
        submission = {
            'student_id': student_id,
            'student_name': f"{student.get('firstname', '')} {student.get('lastname', '')}",
            'submission_files': submission_files,
            'comment': comment,
            'submitted_date': datetime.datetime.now().strftime('%Y-%m-%d'),
            'submitted_time': datetime.datetime.now().strftime('%H:%M:%S'),
            'has_submitted': True,
            'grade': None,  # Initial grade is null
            'feedback': ''   # Initial feedback is empty
        }
        
        # Check if student has already submitted
        existing_submission_index = None
        for i, s in enumerate(task.get('submissions', [])):
            if s.get('student_id') == student_id:
                existing_submission_index = i
                break
        
        if existing_submission_index is not None:
            # If student is resubmitting, we need to handle previous files
            existing_files = task['submissions'][existing_submission_index].get('submission_files', [])
            
            # Remove old files from GridFS
            for old_file in existing_files:
                if 'file_id' in old_file and ObjectId.is_valid(old_file['file_id']):
                    try:
                        # Delete the file from GridFS
                        mongo.fs.delete(ObjectId(old_file['file_id']))
                    except Exception as e:
                        logger.warning(f"Could not delete old file {old_file['file_id']}: {e}")
            
            # Update existing submission
            result = mongo.tasks_collection.update_one(
                {'_id': ObjectId(task_id)},
                {'$set': {
                    f'submissions.{existing_submission_index}': submission
                }}
            )
            submission_message = "Task submission updated successfully"
        else:
            # Add new submission
            result = mongo.tasks_collection.update_one(
                {'_id': ObjectId(task_id)},
                {'$push': {'submissions': submission}}
            )
            submission_message = "Task submitted successfully"
        
        if result.modified_count == 0:
            # If submission failed, clean up uploaded files
            for file_info in submission_files:
                if 'file_id' in file_info and ObjectId.is_valid(file_info['file_id']):
                    try:
                        mongo.fs.delete(ObjectId(file_info['file_id']))
                    except:
                        pass
            return jsonify({"error": "Failed to submit task"}), 500
        
        # Broadcast real-time update event for task submission
        try:
            event_manager.broadcast({
                'type': 'task_submission_update',
                'task_id': task_id,
                'student_id': student_id,
                'submission_data': submission
            })
        except Exception as e:
            logger.error(f"Error broadcasting task submission event: {e}")
        
        return jsonify({
            "message": submission_message,
            "task_id": task_id,
            "student_id": student_id,
            "files_submitted": len(submission_files),
            "submission": submission
        }), 200
        
    except Exception as e:
        logger.error(f"Error submitting task: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/task/grade/<task_id>/<student_id>', methods=['POST'])
def grade_submission(task_id, student_id):
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data or 'grade' not in data:
            return jsonify({"error": "Grade is required"}), 400
            
        # Validate IDs format
        if not ObjectId.is_valid(task_id):
            return jsonify({"error": "Invalid task ID format"}), 400
            
        if not ObjectId.is_valid(student_id):
            return jsonify({"error": "Invalid student ID format"}), 400
            
        # Verify task exists
        task = mongo.tasks_collection.find_one({'_id': ObjectId(task_id)})
        if not task:
            return jsonify({"error": "Task not found"}), 404
            
        # Find submission index for the student
        submission_index = None
        for i, s in enumerate(task.get('submissions', [])):
            if s.get('student_id') == student_id:
                submission_index = i
                break
        
        if submission_index is None:
            return jsonify({"error": "Submission not found"}), 404
        
        # Prepare update data
        submission_update = {
            'grade': data['grade'],
            'feedback': data.get('feedback', '')
        }
        
        # Update the submission
        result = mongo.tasks_collection.update_one(
            {'_id': ObjectId(task_id)},
            {'$set': {
                f'submissions.{submission_index}.grade': data['grade'],
                f'submissions.{submission_index}.feedback': data.get('feedback', '')
            }}
        )
        
        # Retrieve the updated task to get the latest submission data
        updated_task = mongo.tasks_collection.find_one({'_id': ObjectId(task_id)})
        updated_submission = updated_task['submissions'][submission_index]
        
        # Prepare submission update data for broadcast
        broadcast_submission_data = {
            'student_id': student_id,
            'student_name': updated_submission.get('student_name', ''),
            'grade': data['grade'],
            'feedback': data.get('feedback', ''),
            'submission_files': updated_submission.get('submission_files', [])
        }
        
        # Broadcast real-time update event for submission grading
        try:
            event_manager.broadcast({
                'type': 'task_submission_update',
                'task_id': task_id,
                'student_id': student_id,
                'submission_data': broadcast_submission_data
            })
        except Exception as e:
            logger.error(f"Error broadcasting submission grading event: {e}")
        
        return jsonify({
            "message": "Submission graded successfully",
            "task_id": task_id,
            "student_id": student_id,
            "grade": data['grade'],
            "submission": broadcast_submission_data
        }), 200
        
    except Exception as e:
        logger.error(f"Error grading submission: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/file/<file_id>', methods=['GET'])
def get_file(file_id):
    """Retrieve a file from GridFS by its ID"""
    try:
        # Validate ID format
        if not ObjectId.is_valid(file_id):
            return jsonify({"error": "Invalid file ID format"}), 400
        
        # Retrieve file from GridFS
        try:
            grid_fs_file = mongo.fs.get(ObjectId(file_id))
        except:
            return jsonify({"error": "File not found in storage"}), 404
            
        # Return the file with proper content type
        return Response(
            grid_fs_file.read(),
            mimetype=grid_fs_file.metadata.get('content_type', 'application/pdf'),
            headers={
                'Content-Disposition': f'inline; filename="{grid_fs_file.metadata.get("original_filename", "document.pdf")}"'
            }
        )
        
    except Exception as e:
        logger.error(f"Error fetching file: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/file/by-name/<filename>', methods=['GET'])
def get_file_by_name(filename):
    """Retrieve the most recent file by filename"""
    try:
        # Find the file in GridFS by filename - get the most recent
        grid_out = mongo.fs.find_one({"filename": filename}, sort=[("uploadDate", -1)])
        
        if not grid_out:
            return jsonify({"error": f"File '{filename}' not found"}), 404
            
        # Return the file with proper content type
        return Response(
            grid_out.read(),
            mimetype=grid_out.metadata.get('content_type', 'application/pdf'),
            headers={
                'Content-Disposition': f'inline; filename="{filename}"'
            }
        )
        
    except Exception as e:
        logger.error(f"Error fetching file by name: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/task/<task_id>/submission/<student_id>/file/<file_id>', methods=['GET'])
def get_submission_file(task_id, student_id, file_id):
    """Get a submission file with proper ownership verification"""
    try:
        # Validate all IDs
        if not all(ObjectId.is_valid(id) for id in [task_id, student_id, file_id]):
            return jsonify({"error": "Invalid ID format"}), 400

        # Find the task with the submission
        task = mongo.tasks_collection.find_one({
            '_id': ObjectId(task_id),
            'submissions.student_id': student_id
        })
        
        if not task:
            return jsonify({"error": "Task or submission not found"}), 404

        # Find the specific submission
        submission = next(
            (s for s in task.get('submissions', []) if s.get('student_id') == student_id),
            None
        )
        
        if not submission:
            return jsonify({"error": "Submission not found"}), 404

        # Verify the file exists in this submission - more lenient check
        file_exists = any(
            str(f.get('file_id')) == file_id 
            for f in submission.get('submission_files', [])
        )
        
        # Even if file validation fails, we'll try to retrieve the file if it belongs to this task/student
        # This helps with UI sessions that may have stale references
        try:
            grid_fs_file = mongo.fs.get(ObjectId(file_id))
            # Verify file metadata if available
            if grid_fs_file.metadata:
                metadata_task_id = grid_fs_file.metadata.get('task_id')
                metadata_student_id = grid_fs_file.metadata.get('student_id')
                # If metadata confirms this is the right file, allow access despite missing in submission_files
                if metadata_task_id == task_id and metadata_student_id == student_id:
                    file_exists = True
        except:
            # If we can't get the file at all, continue with normal error flow
            pass
            
        if not file_exists:
            return jsonify({"error": "File not associated with this submission"}), 404

        # Retrieve file from GridFS
        try:
            grid_fs_file = mongo.fs.get(ObjectId(file_id))
        except:
            return jsonify({"error": "File not found in storage"}), 404
            
        # Return the file
        return Response(
            grid_fs_file.read(),
            mimetype=grid_fs_file.content_type,
            headers={
                'Content-Disposition': f'inline; filename="{grid_fs_file.filename}"'
            }
        )
        
    except Exception as e:
        logger.error(f"Error fetching submission file: {e}")
        return jsonify({"error": str(e)}), 500
        
@app.route('/task/student/<task_id>/<student_id>', methods=['GET'])
def get_student_task_detail(task_id, student_id):
    try:
        # Validate IDs format
        if not ObjectId.is_valid(task_id):
            return jsonify({"error": "Invalid task ID format"}), 400
            
        if not ObjectId.is_valid(student_id):
            return jsonify({"error": "Invalid student ID format"}), 400
            
        # Get the task
        task = mongo.tasks_collection.find_one({'_id': ObjectId(task_id)})
        if not task:
            return jsonify({"error": "Task not found"}), 404
            
        # Verify student exists
        student = mongo.users_collection.find_one({'_id': ObjectId(student_id)})
        if not student:
            return jsonify({"error": "Student not found"}), 404
            
        # Get course information
        course_id = task.get('course_id')
        course = mongo.courses_collection.find_one({'_id': ObjectId(course_id)})
        if course:
            task['course_name'] = course.get('course_name', 'Unknown Course')
            
        # Add submission information if exists
        # Check for student ID in both string and ObjectId format
        submission = None
        
        # First try the direct match
        for sub in task.get('submissions', []):
            sub_student_id = sub.get('student_id')
            print(sub_student_id, student_id)
            # Compare as strings to handle both ObjectId and string formats
            if str(sub_student_id) == str(student_id):
                submission = sub
                break
        
        if submission:
            # Make sure to clean any ObjectId references for JSON serialization
            cleaned_submission = json_util.loads(json_util.dumps(submission))
            task['submission'] = cleaned_submission
            task['has_submitted'] = True
            task['submission_date'] = f"{submission.get('submitted_date', 'N/A')} {submission.get('submitted_time', '')}"
        else:
            task['has_submitted'] = False
            task['submission_date'] = 'Not submitted'
            task['submission'] = {
                'student_id': student_id,
                'submission_files': [],
                'comment': '',
                'submitted_date': '',
                'submitted_time': '',
                'grade': None,
                'feedback': ''
            }
            
        # Clean the task object for JSON serialization
        cleaned_task = json_util.loads(json_util.dumps(task))
        
        return jsonify(cleaned_task)
        
    except Exception as e:
        logger.error(f"Error getting student task detail: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/course/by-name/<course_name>', methods=['GET'])
def get_course_by_name(course_name):
    try:
        # Find the course by name
        course = mongo.courses_collection.find_one({"course_name": course_name})
        
        if not course:
            return jsonify({"error": "Course not found"}), 404
            
        # Return the course data
        return Response(
            json_util.dumps(course),
            mimetype='application/json'
        )
        
    except Exception as e:
        logger.error(f"Error getting course by name: {e}")
        return jsonify({"error": str(e)}), 500
    
# =================================Events ========================

@app.route('/event', methods=['POST'])
def add_event():
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['selected_date', 'event', 'time', 'course_id', 'course_name', 'user_id']
        if not data or any(field not in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400
            
        selected_date = data.get('selected_date')
        event_description = data.get('event')
        event_time = data.get('time')
        course_id = data.get('course_id')
        course_name = data.get('course_name')
        user_id = data.get('user_id')
        
        # New: Add task_id field to link event to specific task
        task_id = data.get('task_id', '')  # Optional, empty string if not provided
        
        # Get the std_can_update_task flag (default to True if not provided)
        std_can_update_task = data.get('std_can_update_task', True)
        
        # Ensure std_can_update_task is a boolean
        std_can_update_task = bool(std_can_update_task)
        
        # Ensure course_id is valid
        if not ObjectId.is_valid(course_id):
            return jsonify({"error": "Invalid course ID format"}), 400
            
        # If task_id is provided, validate it
        if task_id and not ObjectId.is_valid(task_id):
            return jsonify({"error": "Invalid task ID format"}), 400
            
        # If task_id is provided, verify it exists and belongs to the course
        if task_id:
            task = mongo.tasks_collection.find_one({'_id': ObjectId(task_id)})
            if not task:
                return jsonify({"error": "Task not found"}), 404
                
            if task.get('course_id') != course_id:
                return jsonify({"error": "Task does not belong to the specified course"}), 400
            
        # Validate date format and check if it's a past date
        try:
            date_obj = datetime.datetime.strptime(selected_date, "%Y-%m-%d").date()
            today = datetime.datetime.today().date()
            
            if date_obj < today:
                return jsonify({"error": "Cannot add events to past dates"}), 400
        except ValueError:
            return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400
        
        # Verify user exists
        user = mongo.users_collection.find_one({'_id': ObjectId(user_id)})
        if not user:
            return jsonify({"error": "User not found"}), 404
            
        # Verify course exists
        course = mongo.courses_collection.find_one({'_id': ObjectId(course_id)})
        if not course:
            return jsonify({"error": "Course not found"}), 404
            
        # Check if an event for this course already exists
        existing_event = mongo.events_collection.find_one({"course_name": course_name})
        
        if existing_event:
            # Get the event document ID as string
            event_doc_id = str(existing_event["_id"])
            
            # Check if the date key exists in the event dictionary
            if selected_date in existing_event.get("event", {}):
                # Get the current array of events for this date
                current_events = existing_event.get("event", {}).get(selected_date, [])
                
                # Calculate the next index
                next_index = len(current_events)
                
                # Add new event to the same date with index, event ID, task_id and std_can_update_task
                # Updated structure: [index, time, desc, course_name, event_id, date, user_id, std_can_update_task, task_id]
                result = mongo.events_collection.update_one(
                    {"_id": existing_event["_id"]},
                    {"$push": {f"event.{selected_date}": [next_index, event_time, event_description, course_name, 
                                                         event_doc_id, selected_date, user_id, std_can_update_task, task_id]}}
                )
                
                if result.modified_count == 0:
                    return jsonify({"error": "Failed to add event"}), 500
            else:
                # Add the date to the event dictionary with first event having index 0, event ID, task_id and std_can_update_task
                result = mongo.events_collection.update_one(
                    {"_id": existing_event["_id"]},
                    {"$set": {f"event.{selected_date}": [[0, event_time, event_description, course_name, 
                                                         event_doc_id, selected_date, user_id, std_can_update_task, task_id]]}}
                )
                
                if result.modified_count == 0:
                    return jsonify({"error": "Failed to add event"}), 500
                    
            return jsonify({
                "message": "Event added successfully to existing course",
                "course": course_name,
                "date": selected_date,
                "event_id": event_doc_id,
                "std_can_update_task": std_can_update_task,
                "task_id": task_id
            }), 200
        else:
            # Create a new document ID for this event
            new_event_id = ObjectId()
            event_doc_id = str(new_event_id)
            
            # Insert new event document for this course with first event having index 0, event ID, task_id and std_can_update_task
            event_doc = {
                "_id": new_event_id,  # Use the pre-generated ID
                "date": selected_date,
                "time": event_time,
                "course_id": course_id,
                "course_name": course_name,
                "user_id": user_id,
                "user_name": f"{user.get('firstname', '')} {user.get('lastname', '')}",
                "created_at": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "event": {selected_date: [[0, event_time, event_description, course_name, 
                                         event_doc_id, selected_date, user_id, std_can_update_task, task_id]]}
            }
            
            result = mongo.events_collection.insert_one(event_doc)
            
            return jsonify({
                "message": "Event created successfully",
                "course": course_name,
                "date": selected_date,
                "event_id": event_doc_id,
                "std_can_update_task": std_can_update_task,
                "task_id": task_id
            }), 201
            
    except Exception as e:
        logger.error(f"Error adding event: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/api/update-event', methods=['PUT'])
def update_event_endpoint():
    """
    Update a specific event in the events collection
    
    Expected JSON payload:
    {
        "document_id": "6803f206f3d0399487412503",
        "date_key": "2025-04-19",
        "event_data": {
            "index": 0,                     # Optional - if not provided, find by time
            "time": "09:56:57 PM",          # Required for finding by time
            "description": "Updated english test",
            "course_name": "English Language",
            "std_can_update_task": true     # Optional - student permission to update
        }
    }
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data or 'document_id' not in data or 'date_key' not in data or 'event_data' not in data:
            return jsonify({"error": "Missing required fields"}), 400
            
        document_id = data['document_id']
        date_key = data['date_key']
        event_data = data['event_data']
        
        # Validate document_id
        if not ObjectId.is_valid(document_id):
            return jsonify({"error": "Invalid document_id format"}), 400
            
        # Find the document
        document = mongo.events_collection.find_one({"_id": ObjectId(document_id)})
        if not document:
            return jsonify({"error": "Document not found"}), 404
            
        # Check if the date exists in the document
        if date_key not in document.get("event", {}):
            return jsonify({"error": f"Date {date_key} not found in document"}), 404
            
        # Get events for this date
        date_events = document["event"][date_key]
        
        # Find the event to update - either by index or by time
        event_index = None
        
        if 'index' in event_data and isinstance(event_data['index'], int):
            # Use provided index
            event_index = event_data['index']
            if event_index < 0 or event_index >= len(date_events):
                return jsonify({
                    "error": f"Invalid event index. Must be between 0 and {len(date_events)-1}"
                }), 400
        elif 'time' in event_data:
            # Find by time
            for i, event_arr in enumerate(date_events):
                if len(event_arr) > 1 and event_arr[1] == event_data['time']:
                    event_index = i
                    break
            
            if event_index is None:
                return jsonify({
                    "error": f"No event found with time '{event_data['time']}' on date {date_key}",
                    "available_events": date_events
                }), 404
        else:
            return jsonify({"error": "Either event index or time must be provided"}), 400
            
        # Get the current event
        current_event = date_events[event_index]
        
        # Create updated event array with same structure as original
        updated_event = current_event.copy()
        
        # Update fields based on provided data
        # Assuming the structure is [0, time, description, course_name, document_id, date, user_id, std_can_update_task]
        if 'time' in event_data and event_data['time'] != current_event[1]:
            updated_event[1] = event_data['time']
            
        if 'description' in event_data and event_data['description'] != current_event[2]:
            updated_event[2] = event_data['description']
            
        if 'course_name' in event_data and len(updated_event) >= 4:
            updated_event[3] = event_data['course_name']
        
        # Update std_can_update_task if provided
        if 'std_can_update_task' in event_data:
            std_can_update_task = bool(event_data['std_can_update_task'])
            
            # Check if we need to add or update the std_can_update_task field
            if len(updated_event) >= 8:
                # Update existing field
                updated_event[7] = std_can_update_task
            elif len(updated_event) == 7:
                # Add the field at index 7
                updated_event.append(std_can_update_task)
            
        # Update the event in the database
        result = mongo.events_collection.update_one(
            {"_id": ObjectId(document_id)},
            {"$set": {f"event.{date_key}.{event_index}": updated_event}}
        )
        
        # Check if update was successful
        if result.matched_count == 0:
            return jsonify({"error": "Event not found"}), 404
            
        if result.modified_count == 0:
            return jsonify({"message": "No changes were made"}), 200
            
        return jsonify({
            "success": True,
            "message": "Event updated successfully",
            "document_id": document_id,
            "date": date_key,
            "index": event_index,
            "updated_event": updated_event
        }), 200
        
    except Exception as e:
        logger.error(f"Error updating event: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/api/update-event-permission', methods=['PUT'])
def update_event_permission():
    """
    Update the std_can_update_task field of an event from events collection based on time
    
    Expected JSON payload:
    {
        "event_data": [document_id, date, time],
        "std_can_update_task": true/false
    }
    """
    try:
        data = request.json
        
        if not data or 'event_data' not in data or 'std_can_update_task' not in data:
            return jsonify({"error": "Missing required parameters"}), 400
        
        # Extract data from payload
        event_data = data['event_data']
        std_can_update_task = bool(data['std_can_update_task'])  # Ensure boolean value
        
        # Check if we have at least the required parameters
        if not isinstance(event_data, list) or len(event_data) < 3:
            return jsonify({"error": "event_data must be a list with [document_id, date, time]"}), 400
        
        # Extract the values - ignore index if provided
        # If the old format [index, document_id, date, time] is used, adjust accordingly
        if len(event_data) >= 4:  # Old format with index
            document_id = event_data[1]
            date_key = event_data[2]
            time_value = event_data[3]
        else:  # New format without index
            document_id = event_data[0]
            date_key = event_data[1]
            time_value = event_data[2]
        
        # Validate document_id
        if not ObjectId.is_valid(document_id):
            return jsonify({"error": "Invalid document_id format"}), 400
        
        # Find the document
        document = mongo.events_collection.find_one({"_id": ObjectId(document_id)})
        if not document:
            return jsonify({"error": "Document not found"}), 404
        
        # Check if the date exists in the document
        if date_key not in document.get("event", {}):
            return jsonify({"error": f"Date {date_key} not found in document"}), 404
        
        # Get all events for this date
        date_events = document["event"][date_key]
        
        # Find the event with matching time
        found_index = None
        found_event = None
        
        for i, event in enumerate(date_events):
            if len(event) >= 2:  # Make sure the event has at least 2 elements
                event_time = str(event[1]).strip()  # Assuming time is at position 1
                if event_time == str(time_value).strip():
                    found_index = i
                    found_event = event
                    break
        
        # If no matching event found
        if found_index is None:
            return jsonify({
                "error": f"No event found with time '{time_value}' on date '{date_key}'",
                "available_times": [e[1] for e in date_events if len(e) >= 2]
            }), 404
        
        # Update the event with std_can_update_task
        updated_event = found_event.copy()
        
        # Make sure we have the expected structure
        # Structure should be [index, time, description, course_name, document_id, date, user_id, std_can_update_task]
        if len(updated_event) >= 8:
            # Update existing field
            updated_event[7] = std_can_update_task
        elif len(updated_event) == 7:
            # Add the field
            updated_event.append(std_can_update_task)
        else:
            # If the structure is not complete, we need to ensure it has the minimum elements
            while len(updated_event) < 7:
                updated_event.append("")  # Pad with empty strings if needed
            updated_event.append(std_can_update_task)  # Then add permission
            
        # Update the specific event in the array
        result = mongo.events_collection.update_one(
            {"_id": ObjectId(document_id)},
            {"$set": {f"event.{date_key}.{found_index}": updated_event}}
        )
        
        if result.matched_count == 0:
            return jsonify({"error": "Failed to update event permission"}), 500
        
        return jsonify({
            "success": True,
            "message": f"Updated permission for event with time '{time_value}' on date '{date_key}'",
            "document_id": document_id,
            "std_can_update_task": std_can_update_task,
            "updated_event": updated_event
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/events/date/<date>', methods=['GET'])
def get_events_by_date(date):
    try:
        # Validate date format
        try:
            datetime.datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400
            
        # Get events for the specified date by checking the event object
        query = {f"event.{date}": {"$exists": True}}
        events = mongo.events_collection.find(query)
        
        # Format the response with only the events for the requested date
        events_list = list(events)
        
        if not events_list:
            return jsonify({"events": []}), 200
        
        # Extract only the events for the specified date from each document
        filtered_events = []
        for doc in events_list:
            if date in doc.get("event", {}):
                # Create a simplified document with only the relevant date events
                filtered_doc = {
                    "_id": doc["_id"],
                    "course_id": doc.get("course_id"),
                    "course_name": doc.get("course_name"),
                    "user_id": doc.get("user_id"),
                    "user_name": doc.get("user_name"),
                    "created_at": doc.get("created_at"),
                    "event": {date: doc["event"][date]}
                }
                filtered_events.append(filtered_doc)
        
        return Response(
            json_util.dumps(filtered_events),
            mimetype='application/json'
        )
        
    except Exception as e:
        logger.error(f"Error getting events by date: {e}")
        return jsonify({"error": str(e)}), 500
    
# Endpoint to get all events for a user
@app.route('/events/user/<user_id>', methods=['GET'])
def get_user_events(user_id):
    try:
        # Validate user ID
        if not ObjectId.is_valid(user_id):
            return jsonify({"error": "Invalid user ID format"}), 400
            
        # Get all events for the user
        events = list(mongo.events_collection.find({"user_id": user_id}))
        
        return Response(
            json_util.dumps(events),
            mimetype='application/json'
        )
        
    except Exception as e:
        logger.error(f"Error getting user events: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/all-events', methods=['GET'])
def get_all_events():
    try:
        # Fetch all events from the collection
        all_events = list(mongo.events_collection.find({}))
        
        # Return the events as JSON
        return Response(
            json_util.dumps(all_events),
            mimetype='application/json'
        )
        
    except Exception as e:
        logger.error(f"Error fetching all events: {e}")
        return jsonify({"error": str(e)}), 500

# Endpoint to delete an event
@app.route('/event/<event_id>', methods=['DELETE'])
def delete_event(event_id):
    try:
        # Validate event ID
        if not ObjectId.is_valid(event_id):
            return jsonify({"error": "Invalid event ID format"}), 400
            
        # Check if event exists
        event = mongo.events_collection.find_one({"_id": ObjectId(event_id)})
        if not event:
            return jsonify({"error": "Event not found"}), 404
            
        # Delete the event
        result = mongo.events_collection.delete_one({"_id": ObjectId(event_id)})
        
        if result.deleted_count == 0:
            return jsonify({"error": "Failed to delete event"}), 500
            
        return jsonify({
            "message": "Event deleted successfully",
            "event_id": event_id
        }), 200
        
    except Exception as e:
        logger.error(f"Error deleting event: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/delete-event', methods=['DELETE'])
def delete_event_endpoint():
    """
    Delete an event from events collection based on time
    
    Expected JSON payload:
    {
        "event_data": [document_id, date, time]
    }
    """
    try:
        data = request.json
        
        if not data or 'event_data' not in data:
            return jsonify({"error": "Missing required event_data"}), 400
        
        # Extract data from payload
        event_data = data['event_data']
        
        # Check if we have at least the required parameters
        if not isinstance(event_data, list) or len(event_data) < 3:
            return jsonify({"error": "event_data must be a list with [document_id, date, time]"}), 400
        
        # Extract the values - ignore index if provided
        # If the old format [index, document_id, date, time] is used, adjust accordingly
        if len(event_data) >= 4:  # Old format with index
            document_id = event_data[1]
            date_key = event_data[2]
            time_value = event_data[3]
        else:  # New format without index
            document_id = event_data[0]
            date_key = event_data[1]
            time_value = event_data[2]
        
        # Validate document_id
        if not ObjectId.is_valid(document_id):
            return jsonify({"error": "Invalid document_id format"}), 400
        
        # Find the document
        document = mongo.events_collection.find_one({"_id": ObjectId(document_id)})
        if not document:
            return jsonify({"error": "Document not found"}), 404
        
        # Check if the date exists in the document
        if date_key not in document.get("event", {}):
            return jsonify({"error": f"Date {date_key} not found in document"}), 404
        
        # Get all events for this date
        date_events = document["event"][date_key]
        
        # Find the event with matching time
        found_index = None
        found_event = None
        
        for i, event in enumerate(date_events):
            if len(event) >= 2:  # Make sure the event has at least 2 elements
                event_time = str(event[1]).strip()  # Assuming time is at position 1
                if event_time == str(time_value).strip():
                    found_index = i
                    found_event = event
                    break
        
        # If no matching event found
        if found_index is None:
            return jsonify({
                "error": f"No event found with time '{time_value}' on date '{date_key}'",
                "available_times": [e[1] for e in date_events if len(e) >= 2]
            }), 404
        
        # Delete the specific event
        # First, unset the specific array element
        unset_result = mongo.events_collection.update_one(
            {"_id": ObjectId(document_id)},
            {"$unset": {f"event.{date_key}.{found_index}": ""}}
        )
        
        # Then pull the null elements to remove the "hole" in the array
        pull_result = mongo.events_collection.update_one(
            {"_id": ObjectId(document_id)},
            {"$pull": {f"event.{date_key}": None}}
        )
        
        # If the array is now empty, clean up the empty date key
        mongo.events_collection.update_one(
            {"_id": ObjectId(document_id), f"event.{date_key}": {"$size": 0}},
            {"$unset": {f"event.{date_key}": ""}}
        )
        
        return jsonify({
            "success": True,
            "message": f"Deleted event with time '{time_value}' on date '{date_key}'",
            "document_id": document_id,
            "deleted_event": found_event
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    


@app.route('/task/check-submission-permission/<task_id>', methods=['GET'])
def check_task_submission_permission(task_id):
    """
    Check if a task is allowed to be submitted based on related events
    
    This endpoint looks for events that are either:
    1. Specifically linked to this task_id, or
    2. Associated with the task's course but without a specific task_id
    
    If any relevant event has std_can_update_task=False, submission is blocked
    """
    try:
        # Validate task_id format
        if not ObjectId.is_valid(task_id):
            return jsonify({"error": "Invalid task ID format"}), 400
            
        # Get the task
        task = mongo.tasks_collection.find_one({'_id': ObjectId(task_id)})
        if not task:
            return jsonify({"error": "Task not found"}), 404
            
        # Get course information
        course_id = task.get('course_id')
        course_name = task.get('course_name', '')
        
        if not course_name:
            # Try to fetch course name if not in task
            course = mongo.courses_collection.find_one({'_id': ObjectId(course_id)})
            if course:
                course_name = course.get('course_name', '')
        
        # Get today's date
        today = datetime.datetime.now().strftime('%Y-%m-%d')
        
        # Find events for today AND past dates that are either:
        # 1. Specifically for this task, or
        # 2. For this course but not specific to any task
        
        # We'll modify the query to check ALL dates, not just today
        course_events = list(mongo.events_collection.find({
            "course_name": course_name
        }))
        
        # Check if any events have std_can_update_task set to False
        can_submit = True
        blocking_event = None
        
        for event_doc in course_events:
            event_dates = event_doc.get("event", {})
            
            # Process each date in the event document
            for date_key, date_events in event_dates.items():
                # Skip future dates - we only care about today and past dates
                try:
                    event_date = datetime.datetime.strptime(date_key, "%Y-%m-%d").date()
                    today_date = datetime.datetime.now().date()
                    
                    # Only check today's date and past dates
                    if event_date > today_date:
                        continue
                        
                    # Check events for this date
                    for event in date_events:
                        # Extended Event structure: 
                        # [index, time, desc, course_name, event_id, date, user_id, std_can_update_task, task_id]
                        
                        # Skip empty/invalid events
                        if not event or len(event) < 7:
                            continue
                            
                        event_task_id = ''
                        if len(event) >= 9:
                            event_task_id = event[8]
                        
                        # Get submission permission flag
                        has_permission_flag = len(event) >= 8
                        permission_denied = has_permission_flag and event[7] is False
                        
                        # Check if this event applies to our task
                        # The event applies if:
                        # 1. It has a task_id that matches our task_id, or
                        # 2. It has no task_id (or empty task_id) - meaning it applies to all tasks in the course
                        is_event_for_this_task = (event_task_id == '' or event_task_id == task_id)
                        
                        if is_event_for_this_task and permission_denied:
                            can_submit = False
                            blocking_event = {
                                "time": event[1],
                                "description": event[2],
                                "course_name": event[3],
                                "date": date_key  # Include the date in the blocking event info
                            }
                            break
                    
                    # If we already found a blocking event, no need to check further
                    if not can_submit:
                        break
                        
                except ValueError:
                    # Skip invalid dates
                    logger.warning(f"Invalid date format found in events: {date_key}")
                    continue
            
            # If we already found a blocking event, no need to check further events
            if not can_submit:
                break
        
        # Return the permission result
        return jsonify({
            "task_id": task_id,
            "course_name": course_name,
            "can_submit": can_submit,
            "date_checked": today,
            "blocking_event": blocking_event
        })
        
    except Exception as e:
        logger.error(f"Error checking submission permission: {e}")
        return jsonify({"error": str(e)}), 500

from urllib.parse import quote_plus
from pymongo import MongoClient
import logging

# Add your MongoDB credentials (securely)
username = "quizapplicationstudents"  # Replace with your actual credentials source
password = "king@App9963"  # Replace with your actual credentials source


@app.route('/health', methods=['GET'])
def health_check():
    """Simple endpoint to verify API is running"""
    return {"status": "ok"}

@app.route('/test-mongo-connection', methods=['GET'])
def test_mongo_connection():
    """Test MongoDB connection and return result"""
    try:
        encoded_username = quote_plus(username)
        encoded_password = quote_plus(password)
        MONGO_URI = f"mongodb+srv://{encoded_username}:{encoded_password}@cluster0.dovxs.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
        
        logging.info("Testing MongoDB connection...")
        client = MongoClient(MONGO_URI)
        db = client["assignment_submitter"]
        
        # Try a simple operation
        collections = db.list_collection_names()
        logging.info(f"MongoDB connection successful. Found collections: {collections}")
        
        return {"status": "success", "message": "MongoDB connection successful"}
    except Exception as e:
        logging.error(f"MongoDB test connection failed: {str(e)}")
        return {"status": "error", "message": str(e)}, 500



if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)

