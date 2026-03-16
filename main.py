import os
import datetime
from flask import Flask, request, jsonify, Blueprint
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt_identity,
    get_jwt,
)
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
from bson import ObjectId
import cloudinary
import cloudinary.uploader
import cloudinary.api


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "super-secret-key-change-in-production")
    MONGO_URI = "mongodb+srv://vishnu:tvmk2006@firstsample.c9yehfj.mongodb.net/firstsample?retryWrites=true&w=majority&appName=firstsample"
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "jwt-super-secret-key-change-in-production")
    CLOUDINARY_CLOUD_NAME = "dpebzsbtj"
    CLOUDINARY_API_KEY = "317852785236772"
    CLOUDINARY_API_SECRET = "GQO2xD1SO-hYiJjzl54CPPK_lTQ"


db = None

DEFAULT_CATEGORIES = [
    "Living Room",
    "Bedroom",
    "Dining",
    "Office Furniture",
]

ORDER_STATUSES = ["Pending", "Confirmed", "Shipped", "Delivered", "Cancelled"]
DEFAULT_LOW_STOCK_THRESHOLD = 5


def init_db(app):
    global db
    client = MongoClient(app.config["MONGO_URI"])
    db = client["firstsample"]


def ensure_default_categories():
    for name in DEFAULT_CATEGORIES:
        db.categories.update_one(
            {"name": name},
            {
                "$setOnInsert": {
                    "name": name,
                    "created_at": datetime.datetime.utcnow(),
                }
            },
            upsert=True,
        )


def ensure_sample_orders():
    if db.orders.count_documents({}) > 0:
        return

    sample_product = db.products.find_one()
    if not sample_product:
        return

    quantity = 1
    unit_price = float(sample_product.get("currentPrice", 0))
    total_price = round(unit_price * quantity, 2)
    now = datetime.datetime.utcnow()

    db.orders.insert_one(
        {
            "customer": {
                "id": "sample-user",
                "name": "Sample Customer",
                "email": "sample.customer@example.com",
                "phone": "+91 9000000000",
            },
            "items": [
                {
                    "productId": str(sample_product.get("_id")),
                    "name": sample_product.get("name", "Unknown Product"),
                    "imageUrl": sample_product.get("imageUrl"),
                    "quantity": quantity,
                    "unitPrice": unit_price,
                    "totalPrice": total_price,
                }
            ],
            "payment": {
                "method": "Cash on Delivery",
                "status": "Pending",
                "transactionRef": None,
            },
            "deliveryAddress": {
                "line1": "Sample Address Line 1",
                "line2": "",
                "city": "Bengaluru",
                "state": "Karnataka",
                "postalCode": "560001",
                "country": "India",
            },
            "pricing": {
                "subtotal": total_price,
                "shipping": 0,
                "total": total_price,
            },
            "status": "Pending",
            "created_at": now,
            "updated_at": now,
        }
    )


def require_admin_claims(claims):
    if claims.get("role") != "admin":
        return jsonify({"message": "Access forbidden: Admins only"}), 403
    return None


def serialize_order(order):
    serialized = dict(order)
    serialized["id"] = str(serialized.pop("_id"))
    if "created_at" in serialized and hasattr(serialized["created_at"], "isoformat"):
        serialized["created_at"] = serialized["created_at"].isoformat()
    if "updated_at" in serialized and hasattr(serialized["updated_at"], "isoformat"):
        serialized["updated_at"] = serialized["updated_at"].isoformat()
    return serialized


auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/signup", methods=["POST"])
def signup():
    data = request.get_json()
    if not data:
        return jsonify({"message": "Request body must be JSON"}), 400

    if not data.get("username") or not data.get("email") or not data.get("password"):
        return jsonify({"message": "Missing required fields (username, email, password)"}), 400

    if db.users.find_one({"username": data["username"]}):
        return jsonify({"message": "Username already exists"}), 400

    if db.users.find_one({"email": data["email"]}):
        return jsonify({"message": "Email already exists"}), 400

    hashed_password = generate_password_hash(data["password"])
    role = data.get("role", "user")

    # Admin creation safeguard (Commented out for development testing)
    # if role == "admin":
    #     role = "user"

    new_user = {
        "username": data["username"],
        "email": data["email"],
        "password_hash": hashed_password,
        "role": role,
        "created_at": datetime.datetime.utcnow(),
    }

    result = db.users.insert_one(new_user)
    return jsonify({
        "message": "User created successfully",
        "role": role,
        "user_id": str(result.inserted_id),
    }), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data:
        return jsonify({"message": "Request body must be JSON"}), 400

    if not data.get("email") or not data.get("password"):
        return jsonify({"message": "Missing required fields (email, password)"}), 400

    user = db.users.find_one({"email": data["email"]})
    if not user or not check_password_hash(user["password_hash"], data["password"]):
        return jsonify({"message": "Invalid credentials"}), 401

    access_token = create_access_token(
        identity=str(user["_id"]),
        additional_claims={"role": user["role"]},
    )

    return jsonify({
        "message": "Login successful",
        "access_token": access_token,
        "user": {
            "id": str(user["_id"]),
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
        },
    }), 200


@auth_bp.route("/admin-only", methods=["GET"])
@jwt_required()
def admin_only():
    claims = get_jwt()
    if claims.get("role") != "admin":
        return jsonify({"message": "Access forbidden: Admins only"}), 403

    return jsonify({"message": f"Welcome Admin! (User ID: {get_jwt_identity()})"}), 200


products_bp = Blueprint("products", __name__)


def parse_product_payload(data, image_url=None, model_3d_url=None):
    def safe_int(val, default=0):
        try:
            return int(val) if val and str(val).strip() else default
        except Exception:
            return default

    def safe_float(val, default=0.0):
        try:
            return float(val) if val and str(val).strip() else default
        except Exception:
            return default

    raw_category = (data.get("category") or "").strip()
    if not raw_category:
        return None, (jsonify({"message": "Category is required"}), 400)

    if not db.categories.find_one({"name": raw_category}):
        return None, (jsonify({"message": "Invalid category. Create category first."}), 400)

    return {
        "name": data.get("name", "Unnamed Product"),
        "amountInStock": safe_int(data.get("amountInStock")),
        "currentPrice": safe_float(data.get("currentPrice")),
        "previousPrice": safe_float(data.get("previousPrice"), safe_float(data.get("currentPrice"))),
        "deliveryPrice": safe_float(data.get("deliveryPrice")),
        "deliveryInDays": safe_int(data.get("deliveryInDays", 7)),
        "isAmazonChoice": data.get("isAmazonChoice", "false").lower() == "true",
        "category": raw_category,
        "sku": data.get("sku", ""),
        "description": data.get("description", ""),
        "imageUrl": image_url,
        "model3DUrl": model_3d_url,
        "created_at": datetime.datetime.utcnow(),
    }, None


@products_bp.route("/", methods=["GET"])
def get_products():
    category = request.args.get("category")
    query = {}
    if category and category != "All":
        query["category"] = category

    products = []
    for product in db.products.find(query):
        product["id"] = str(product.pop("_id"))
        products.append(product)
    return jsonify(products), 200


@products_bp.route("/", methods=["POST"])
@jwt_required()
def create_product():
    admin_error = require_admin_claims(get_jwt())
    if admin_error:
        return admin_error

    data = request.form
    image = request.files.get("image")
    model_file = request.files.get("model3D")

    image_url = None
    model_3d_url = None
    if image:
        try:
            image_url = cloudinary.uploader.upload(image).get("secure_url")
        except Exception as error:
            return jsonify({"message": f"Image upload failed: {str(error)}"}), 500

    if model_file:
        filename = (model_file.filename or "").lower()
        if not filename.endswith(".glb"):
            return jsonify({"message": "Only .glb files are allowed for 3D models"}), 400

        try:
            upload_result = cloudinary.uploader.upload(
                model_file,
                resource_type="raw",
                folder="furniture-models",
                use_filename=True,
                unique_filename=True,
            )
            model_3d_url = upload_result.get("secure_url")
        except Exception as error:
            return jsonify({"message": f"3D model upload failed: {str(error)}"}), 500

    new_product, parse_error = parse_product_payload(
        data,
        image_url=image_url,
        model_3d_url=model_3d_url,
    )
    if parse_error:
        return parse_error

    try:
        result = db.products.insert_one(new_product)
        new_product["id"] = str(result.inserted_id)
        new_product.pop("_id", None)
        new_product["created_at"] = new_product["created_at"].isoformat()
        return jsonify(new_product), 201
    except Exception as error:
        return jsonify({"message": f"Server error: {str(error)}"}), 500


@products_bp.route("/<product_id>", methods=["DELETE"])
@jwt_required()
def delete_product(product_id):
    admin_error = require_admin_claims(get_jwt())
    if admin_error:
        return admin_error

    try:
        oid = ObjectId(product_id)
    except Exception:
        return jsonify({"message": "Invalid product id"}), 400

    result = db.products.delete_one({"_id": oid})
    if result.deleted_count == 0:
        return jsonify({"message": "Product not found"}), 404

    return jsonify({"message": "Product deleted successfully"}), 200


categories_bp = Blueprint("categories", __name__)


@categories_bp.route("/", methods=["GET"])
def get_categories():
    categories = []
    for category in db.categories.find().sort("name", 1):
        category["id"] = str(category.pop("_id"))
        if "created_at" in category and hasattr(category["created_at"], "isoformat"):
            category["created_at"] = category["created_at"].isoformat()
        categories.append(category)
    return jsonify(categories), 200


@categories_bp.route("/", methods=["POST"])
@jwt_required()
def create_category():
    admin_error = require_admin_claims(get_jwt())
    if admin_error:
        return admin_error

    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"message": "Category name is required"}), 400

    if db.categories.find_one({"name": name}):
        return jsonify({"message": "Category already exists"}), 400

    new_category = {"name": name, "created_at": datetime.datetime.utcnow()}
    result = db.categories.insert_one(new_category)

    return jsonify({
        "id": str(result.inserted_id),
        "name": name,
        "created_at": new_category["created_at"].isoformat(),
    }), 201


@categories_bp.route("/<category_id>", methods=["PUT"])
@jwt_required()
def update_category(category_id):
    admin_error = require_admin_claims(get_jwt())
    if admin_error:
        return admin_error

    data = request.get_json() or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"message": "Category name is required"}), 400

    try:
        oid = ObjectId(category_id)
    except Exception:
        return jsonify({"message": "Invalid category id"}), 400

    existing = db.categories.find_one({"_id": oid})
    if not existing:
        return jsonify({"message": "Category not found"}), 404

    if db.categories.find_one({"name": new_name, "_id": {"$ne": oid}}):
        return jsonify({"message": "Another category with this name already exists"}), 400

    db.categories.update_one({"_id": oid}, {"$set": {"name": new_name}})
    db.products.update_many({"category": existing["name"]}, {"$set": {"category": new_name}})

    return jsonify({"id": category_id, "name": new_name}), 200


@categories_bp.route("/<category_id>", methods=["DELETE"])
@jwt_required()
def delete_category(category_id):
    admin_error = require_admin_claims(get_jwt())
    if admin_error:
        return admin_error

    try:
        oid = ObjectId(category_id)
    except Exception:
        return jsonify({"message": "Invalid category id"}), 400

    existing = db.categories.find_one({"_id": oid})
    if not existing:
        return jsonify({"message": "Category not found"}), 404

    products_using = db.products.count_documents({"category": existing["name"]})
    if products_using > 0:
        return jsonify({
            "message": "Category is in use by products. Reassign products before deleting.",
            "products_using_category": products_using,
        }), 400

    db.categories.delete_one({"_id": oid})
    return jsonify({"message": "Category deleted successfully"}), 200


orders_bp = Blueprint("orders", __name__)


@orders_bp.route("/", methods=["GET"])
@jwt_required()
def get_orders():
    admin_error = require_admin_claims(get_jwt())
    if admin_error:
        return admin_error

    status = request.args.get("status")
    query = {}
    if status and status in ORDER_STATUSES:
        query["status"] = status

    orders = [serialize_order(order) for order in db.orders.find(query).sort("created_at", -1)]
    return jsonify(orders), 200


@orders_bp.route("/", methods=["POST"])
@jwt_required()
def create_order():
    data = request.get_json() or {}
    items = data.get("items") or []

    if not isinstance(items, list) or len(items) == 0:
        return jsonify({"message": "Order items are required"}), 400

    customer_id = get_jwt_identity()
    try:
        user = db.users.find_one({"_id": ObjectId(customer_id)})
    except Exception:
        user = None

    decremented = []
    order_items = []
    subtotal = 0.0

    for item in items:
        product_id = item.get("productId")
        quantity = int(item.get("quantity", 0)) if str(item.get("quantity", "")).strip() else 0

        if not product_id or quantity <= 0:
            for rollback in decremented:
                db.products.update_one({"_id": rollback["oid"]}, {"$inc": {"amountInStock": rollback["quantity"]}})
            return jsonify({"message": "Each order item requires productId and quantity > 0"}), 400

        try:
            oid = ObjectId(product_id)
        except Exception:
            for rollback in decremented:
                db.products.update_one({"_id": rollback["oid"]}, {"$inc": {"amountInStock": rollback["quantity"]}})
            return jsonify({"message": f"Invalid product id: {product_id}"}), 400

        product = db.products.find_one({"_id": oid})
        if not product:
            for rollback in decremented:
                db.products.update_one({"_id": rollback["oid"]}, {"$inc": {"amountInStock": rollback["quantity"]}})
            return jsonify({"message": f"Product not found: {product_id}"}), 404

        stock_update = db.products.update_one(
            {"_id": oid, "amountInStock": {"$gte": quantity}},
            {"$inc": {"amountInStock": -quantity}},
        )

        if stock_update.matched_count == 0:
            for rollback in decremented:
                db.products.update_one({"_id": rollback["oid"]}, {"$inc": {"amountInStock": rollback["quantity"]}})
            return jsonify({
                "message": f"Insufficient stock for product: {product.get('name', product_id)}",
                "productId": product_id,
            }), 400

        decremented.append({"oid": oid, "quantity": quantity})

        unit_price = float(product.get("currentPrice", 0))
        line_total = round(unit_price * quantity, 2)
        subtotal += line_total

        order_items.append({
            "productId": str(oid),
            "name": product.get("name", "Unknown Product"),
            "imageUrl": product.get("imageUrl"),
            "quantity": quantity,
            "unitPrice": unit_price,
            "totalPrice": line_total,
        })

    shipping = 0.0
    total = round(subtotal + shipping, 2)
    now = datetime.datetime.utcnow()
    delivery = data.get("deliveryAddress") or {}

    new_order = {
        "customer": {
            "id": str(user.get("_id")) if user else str(customer_id),
            "name": user.get("username", "Customer") if user else "Customer",
            "email": user.get("email", "") if user else "",
            "phone": data.get("phone"),
        },
        "items": order_items,
        "payment": {
            "method": data.get("paymentMethod") or "Cash on Delivery",
            "status": "Pending",
            "transactionRef": data.get("transactionRef"),
        },
        "deliveryAddress": {
            "line1": delivery.get("line1", "Address Line 1"),
            "line2": delivery.get("line2", ""),
            "city": delivery.get("city", "City"),
            "state": delivery.get("state", "State"),
            "postalCode": delivery.get("postalCode", "000000"),
            "country": delivery.get("country", "India"),
        },
        "pricing": {
            "subtotal": round(subtotal, 2),
            "shipping": shipping,
            "total": total,
        },
        "status": "Pending",
        "created_at": now,
        "updated_at": now,
    }

    result = db.orders.insert_one(new_order)
    created = db.orders.find_one({"_id": result.inserted_id})
    return jsonify(serialize_order(created)), 201


@orders_bp.route("/my", methods=["GET"])
@jwt_required()
def get_my_orders():
    customer_id = str(get_jwt_identity())

    query = {
        "$or": [
            {"customer.id": customer_id},
            {"customer.id": ObjectId(customer_id)} if ObjectId.is_valid(customer_id) else {"customer.id": customer_id},
        ]
    }

    orders = [serialize_order(order) for order in db.orders.find(query).sort("created_at", -1)]
    return jsonify(orders), 200


@orders_bp.route("/<order_id>", methods=["GET"])
@jwt_required()
def get_order_by_id(order_id):
    admin_error = require_admin_claims(get_jwt())
    if admin_error:
        return admin_error

    try:
        oid = ObjectId(order_id)
    except Exception:
        return jsonify({"message": "Invalid order id"}), 400

    order = db.orders.find_one({"_id": oid})
    if not order:
        return jsonify({"message": "Order not found"}), 404

    return jsonify(serialize_order(order)), 200


@orders_bp.route("/<order_id>/status", methods=["PATCH"])
@jwt_required()
def update_order_status(order_id):
    admin_error = require_admin_claims(get_jwt())
    if admin_error:
        return admin_error

    try:
        oid = ObjectId(order_id)
    except Exception:
        return jsonify({"message": "Invalid order id"}), 400

    data = request.get_json() or {}
    new_status = (data.get("status") or "").strip()
    if new_status not in ORDER_STATUSES:
        return jsonify({"message": "Invalid order status", "allowed_statuses": ORDER_STATUSES}), 400

    result = db.orders.update_one(
        {"_id": oid},
        {"$set": {"status": new_status, "updated_at": datetime.datetime.utcnow()}},
    )

    if result.matched_count == 0:
        return jsonify({"message": "Order not found"}), 404

    updated = db.orders.find_one({"_id": oid})
    return jsonify(serialize_order(updated)), 200


profile_bp = Blueprint("profile", __name__)


PROFILE_DEFAULTS = {
    "personal": {
        "fullName": "",
        "phone": "",
        "dateOfBirth": "",
        "gender": "",
    },
    "addresses": [],
    "paymentMethods": [],
    "wishlist": [],
    "notifications": {
        "orderUpdates": True,
        "promotions": True,
        "restockAlerts": True,
        "wishlistDrops": True,
    },
    "settings": {
        "biometricLogin": False,
        "darkMode": False,
        "language": "English",
    },
    "supportTickets": [],
    "returnRequests": [],
}


def get_current_user_doc():
    user_id = get_jwt_identity()
    try:
        oid = ObjectId(user_id)
    except Exception:
        return None, None, (jsonify({"message": "Invalid user identity"}), 400)

    user = db.users.find_one({"_id": oid})
    if not user:
        return None, None, (jsonify({"message": "User not found"}), 404)

    return user, oid, None


def read_profile_data(user):
    profile = user.get("profile") or {}
    return {
        "personal": profile.get("personal") or dict(PROFILE_DEFAULTS["personal"]),
        "addresses": profile.get("addresses") or [],
        "paymentMethods": profile.get("paymentMethods") or [],
        "wishlist": profile.get("wishlist") or [],
        "notifications": profile.get("notifications") or dict(PROFILE_DEFAULTS["notifications"]),
        "settings": profile.get("settings") or dict(PROFILE_DEFAULTS["settings"]),
        "supportTickets": profile.get("supportTickets") or [],
        "returnRequests": profile.get("returnRequests") or [],
    }


def write_profile_field(oid, field_name, value):
    db.users.update_one(
        {"_id": oid},
        {
            "$set": {
                f"profile.{field_name}": value,
                "updated_at": datetime.datetime.utcnow(),
            }
        },
    )


@profile_bp.route("/", methods=["GET"])
@jwt_required()
def get_profile():
    user, _, error = get_current_user_doc()
    if error:
        return error

    profile = read_profile_data(user)
    return jsonify(profile), 200


@profile_bp.route("/personal", methods=["GET", "PUT"])
@jwt_required()
def personal_info():
    user, oid, error = get_current_user_doc()
    if error:
        return error

    if request.method == "GET":
        return jsonify(read_profile_data(user)["personal"]), 200

    payload = request.get_json() or {}
    if not isinstance(payload, dict):
        return jsonify({"message": "Invalid personal info payload"}), 400

    current = read_profile_data(user)["personal"]
    merged = {
        "fullName": payload.get("fullName", current.get("fullName", "")),
        "phone": payload.get("phone", current.get("phone", "")),
        "dateOfBirth": payload.get("dateOfBirth", current.get("dateOfBirth", "")),
        "gender": payload.get("gender", current.get("gender", "")),
    }
    write_profile_field(oid, "personal", merged)
    return jsonify(merged), 200


@profile_bp.route("/addresses", methods=["GET", "PUT"])
@jwt_required()
def addresses():
    user, oid, error = get_current_user_doc()
    if error:
        return error

    if request.method == "GET":
        return jsonify(read_profile_data(user)["addresses"]), 200

    payload = request.get_json() or []
    if not isinstance(payload, list):
        return jsonify({"message": "Addresses must be an array"}), 400

    write_profile_field(oid, "addresses", payload)
    return jsonify(payload), 200


@profile_bp.route("/payment-methods", methods=["GET", "PUT"])
@jwt_required()
def payment_methods():
    user, oid, error = get_current_user_doc()
    if error:
        return error

    if request.method == "GET":
        return jsonify(read_profile_data(user)["paymentMethods"]), 200

    payload = request.get_json() or []
    if not isinstance(payload, list):
        return jsonify({"message": "Payment methods must be an array"}), 400

    write_profile_field(oid, "paymentMethods", payload)
    return jsonify(payload), 200


@profile_bp.route("/wishlist", methods=["GET", "PUT"])
@jwt_required()
def wishlist():
    user, oid, error = get_current_user_doc()
    if error:
        return error

    if request.method == "GET":
        return jsonify(read_profile_data(user)["wishlist"]), 200

    payload = request.get_json() or []
    if not isinstance(payload, list):
        return jsonify({"message": "Wishlist must be an array"}), 400

    write_profile_field(oid, "wishlist", payload)
    return jsonify(payload), 200


@profile_bp.route("/notifications", methods=["GET", "PUT"])
@jwt_required()
def notifications():
    user, oid, error = get_current_user_doc()
    if error:
        return error

    if request.method == "GET":
        return jsonify(read_profile_data(user)["notifications"]), 200

    payload = request.get_json() or {}
    if not isinstance(payload, dict):
        return jsonify({"message": "Notifications payload must be an object"}), 400

    write_profile_field(oid, "notifications", payload)
    return jsonify(payload), 200


@profile_bp.route("/settings", methods=["GET", "PUT"])
@jwt_required()
def settings():
    user, oid, error = get_current_user_doc()
    if error:
        return error

    if request.method == "GET":
        return jsonify(read_profile_data(user)["settings"]), 200

    payload = request.get_json() or {}
    if not isinstance(payload, dict):
        return jsonify({"message": "Settings payload must be an object"}), 400

    write_profile_field(oid, "settings", payload)
    return jsonify(payload), 200


@profile_bp.route("/support-tickets", methods=["GET", "PUT"])
@jwt_required()
def support_tickets():
    user, oid, error = get_current_user_doc()
    if error:
        return error

    if request.method == "GET":
        return jsonify(read_profile_data(user)["supportTickets"]), 200

    payload = request.get_json() or []
    if not isinstance(payload, list):
        return jsonify({"message": "Support tickets must be an array"}), 400

    write_profile_field(oid, "supportTickets", payload)
    return jsonify(payload), 200


@profile_bp.route("/returns", methods=["GET", "PUT"])
@jwt_required()
def returns():
    user, oid, error = get_current_user_doc()
    if error:
        return error

    if request.method == "GET":
        return jsonify(read_profile_data(user)["returnRequests"]), 200

    payload = request.get_json() or []
    if not isinstance(payload, list):
        return jsonify({"message": "Return requests must be an array"}), 400

    write_profile_field(oid, "returnRequests", payload)
    return jsonify(payload), 200


inventory_bp = Blueprint("inventory", __name__)


@inventory_bp.route("/", methods=["GET"])
@jwt_required()
def get_inventory():
    admin_error = require_admin_claims(get_jwt())
    if admin_error:
        return admin_error

    threshold = request.args.get("threshold", type=int) or DEFAULT_LOW_STOCK_THRESHOLD

    products = []
    for product in db.products.find().sort("name", 1):
        stock = int(product.get("amountInStock", 0))
        products.append({
            "id": str(product.get("_id")),
            "name": product.get("name", "Unnamed Product"),
            "sku": product.get("sku", ""),
            "category": product.get("category", ""),
            "amountInStock": stock,
            "lowStock": stock <= threshold,
            "threshold": threshold,
        })

    return jsonify(products), 200


@inventory_bp.route("/alerts/low-stock", methods=["GET"])
@jwt_required()
def get_low_stock_alerts():
    admin_error = require_admin_claims(get_jwt())
    if admin_error:
        return admin_error

    threshold = request.args.get("threshold", type=int) or DEFAULT_LOW_STOCK_THRESHOLD
    alerts = []
    for product in db.products.find({"amountInStock": {"$lte": threshold}}).sort("amountInStock", 1):
        alerts.append({
            "id": str(product.get("_id")),
            "name": product.get("name", "Unnamed Product"),
            "sku": product.get("sku", ""),
            "amountInStock": int(product.get("amountInStock", 0)),
            "threshold": threshold,
        })

    return jsonify(alerts), 200


@inventory_bp.route("/<product_id>/stock", methods=["PATCH"])
@jwt_required()
def update_stock(product_id):
    admin_error = require_admin_claims(get_jwt())
    if admin_error:
        return admin_error

    try:
        oid = ObjectId(product_id)
    except Exception:
        return jsonify({"message": "Invalid product id"}), 400

    data = request.get_json() or {}
    if "amountInStock" not in data:
        return jsonify({"message": "amountInStock is required"}), 400

    try:
        amount = int(data.get("amountInStock"))
    except Exception:
        return jsonify({"message": "amountInStock must be an integer"}), 400

    if amount < 0:
        return jsonify({"message": "amountInStock cannot be negative"}), 400

    result = db.products.update_one({"_id": oid}, {"$set": {"amountInStock": amount}})
    if result.matched_count == 0:
        return jsonify({"message": "Product not found"}), 404

    updated = db.products.find_one({"_id": oid})
    return jsonify({
        "id": str(updated.get("_id")),
        "name": updated.get("name", "Unnamed Product"),
        "amountInStock": int(updated.get("amountInStock", 0)),
    }), 200


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    CORS(app)
    JWTManager(app)

    cloudinary.config(
        cloud_name=app.config["CLOUDINARY_CLOUD_NAME"],
        api_key=app.config["CLOUDINARY_API_KEY"],
        api_secret=app.config["CLOUDINARY_API_SECRET"],
    )

    init_db(app)
    ensure_default_categories()
    ensure_sample_orders()

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(products_bp, url_prefix="/api/products")
    app.register_blueprint(categories_bp, url_prefix="/api/categories")
    app.register_blueprint(orders_bp, url_prefix="/api/orders")
    app.register_blueprint(profile_bp, url_prefix="/api/profile")
    app.register_blueprint(inventory_bp, url_prefix="/api/inventory")

    @app.route("/health", methods=["GET"])
    def health_check():
        return {"status": "healthy"}

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5000)
