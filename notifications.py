import json
import os
import yagmail
import pika
import pymongo
import requests
import sys

"""
IDEAS
tune prefetch count
thread parsers/workers so multiple can run simultaneously
custom URLs for messages
more error handling
-->if there is a parsing error send to the log microservice?

TODOs
Mongo persistence --> check for text file and load in on startup

NEED TO TEST
there might be an issue with typing (str vs int for user and item IDs)
edge cases:
AUCTION ENDS WITH 0 bids or closed prematurely

"""

class MongoCtl:

	def __init__(self, hostport):
		# Initialization boilerplate
		# client
		self.client = pymongo.MongoClient("mongodb://{}/".format(hostport))
		# specific db
		self.db = self.client["notes_db"]
		# sole collection
		self.note_collection = self.db["notes"]

		# todo
		# Persistent memory
		# if os.path.exists("persist_mongo.txt"):
		# 

	# Write into Mongo
	def store_bid(self, item_id, uid):
		record = {"item_id":item_id, "uid":uid}
		self.note_collection.insert_one(record)

	# takes item id as a parameter
	# returns list of all bidders by ID
	def bidders(self, item_id):
		item_query = {"item_id":item_id}
		results = self.note_collection.find(item_query)
		return_list = []
		for entry in results:
			return_list.append(int(entry["uid"]))
		return return_list


class NotificationParser:

	def __init__(self, user_ip_host, user_ip_port, rabbithost, mongohost, sendfrom):
		# establish attributes
		self.packet = None
		self.note_type = None
		# URL for the User Microservice
		# todo get method name correct and test this!!
		self.user_url = "http://" + user_ip_host + ":" +  + "/GetEmailsAPI"
		# Initialize connector to MongoDB
		self.mongo_conn = MongoCtl(mongohost)
		# The address from which the sent emails will originate
		self.sender_email = sendfrom
		# Email password
		# don't hardcode this
		with open('credentials', 'r') as cred:
			self.password = cred.read().rstrip('\n')
		# RabbitMQ consumer boilerplate
		self.connection = pika.BlockingConnection(
    		pika.ConnectionParameters(host=rabbithost)
    	)
		self.channel = self.connection.channel()
		self.channel.queue_declare(queue='notes_queue', durable=True)
		print('[*] Waiting for messages. To exit press CTRL+C')
		self.channel.basic_qos(prefetch_count=1)
		self.channel.basic_consume(queue='notes_queue', on_message_callback=self.callback)
		self.channel.start_consuming()

	# Primary method called by class when consuming new notifications
	def callback(self, ch, method, properties, body):
	    print(" [x] Received notification")
	    self.packet = json.loads(body.decode('utf-8'))
	    self.note_type = self.packet["note_type"]
	    message = self.compose_and_send_messages()
	    print(" [x] Done")
	    ch.basic_ack(delivery_tag=method.delivery_tag)

	# Takes a list of emails to receive same notification
	# message is a tuple of (subject, body) as returned by compose_message() method
	def email_notification(self, emails_list, message):
		for email in emails_list:
			# Pass over edge cases
			if email is not None:
				yag = yagmail.SMTP(self.sender_email, password=self.password)
				yag.send(
					to=email,
					subject=message[0],
					contents=message[1]
					)

	# the main convenience function that parses, composes, and sends the email notifications
	def compose_and_send_messages(self):
		if self.note_type == "customer_service":
			self.email_notification([self.packet["recipient_email"]], ("Reply to DWU Inquiry", self.packet["description"]))
		elif self.note_type == "purchase":
			purchaser_emails = self.get_user_emails([self.packet["user"]])
			self.email_notification([purchaser_emails[self.packet["user"]]], self.compose_message("purchase"))
		elif self.note_type == "newbid":
			emails = self.get_user_emails([self.packet["user"], self.packet["old_user"], self.packet["seller"]])
			self.email_notification([emails[self.packet["old_user"]]], self.compose_message("outbid"))
			self.email_notification([emails[self.packet["user"]]], self.compose_message("highbid"))
			self.email_notification([emails[self.packet["seller"]]], self.compose_message("newbid"))
			self.mongo_conn.store_bid(self.packet["item"], self.packet["user"])
		elif self.note_type == "auction_closed" or self.note_type == "sold":
			bidders_list = self.mongo_conn.bidders(self.item)
			if self.packet["user"] not in bidders_list and self.packet["user"] != None:
				bidders_list.append(self.packet["user"])
			bidder_emails = self.get_user_emails(bidders_list)
			for GUID in bidders_list:
				if GUID == self.packet["user"]:
					self.email_notification([bidder_emails[self.packet["user"]]], self.compose_message("win"))
				else:
					self.email_notification([bidder_emails[GUID]], self.compose_message("lost"))
			seller_email = self.get_user_emails([self.packet["seller"]])
			self.email_notification([seller_email[self.packet["seller"]]], self.compose_message("sold_item"))
		elif self.note_type == "closing":
			bidders_list = self.mongo_conn.bidders(self.packet["item"])
			bidder_emails = self.get_user_emails(bidders_list)
			for GUID in bidders_list:
				self.email_notification([bidder_emails[GUID]], self.compose_message("closing_buyer"))
			seller_email = self.get_user_emails([self.packet["seller"]])
			self.email_notification([seller_email[self.packet["seller"]]], self.compose_message("closing_seller"))
		elif self.note_type == 'watchlist':
			self.email_notification(self.packet["email_list"], self.compose_message("watchlist"))


	# Returns the subject and the body of the message in a tuple -- (subject, body)
	def compose_message(self, message_type):
		if message_type == "purchase":
			subject = "Your purchase is complete!"
			body = "You have successfully purchase {item} for ${dollars}. Congratulations on your purchase!".format(item=self.packet["item_name"], dollars=self.packet["value"])
		elif message_type == "outbid":
			subject = "You have been outbid!"
			body = "You have been outbid on {item}. Return to the site right away to regain the lead!".format(item=self.packet["item_name"])
		elif message_type == "highbid":
			subject = "You now have the highest bid!"
			body = "You have the highest bid of ${dollars} on {item}. Keep an eye on the site to ensure you win!".format(item=self.packet["item_name"], dollars=self.packet["value"])
		elif message_type == "newbid":
			subject = "There has been a new bid on your item!"
			body = "The highest bid on {item} is now ${dollars}. We'll be sure to keep you updated as more bids come it.".format(item=self.packet["item_name"], dollars=self.packet["value"])
		elif message_type == "win":
			if self.note_type == "auction_closed":
				subject = "Congratulations! You had the winning bid!"
				body = "Your bid of ${dollars} on {item} won. The auction is now closed and you will find the item in your cart. You must check out within two days.".format(dollars=self.packet["value"], item=self.packet["item_name"])
			# TODO include link to cart	
			elif self.note_type == "sold":
				subject = "Congratulations! The item is yours!"
				body = "You have agreed to purchase {item} from ${dollars}. The auction is now closed and you will find the item in your cart. You must check out within two days.".format(dollars=self.packet["value"], item=self.packet["item_name"])
		elif message_type == "lost":
			if self.note_type == "auction_closed":
				subject = "An auction has ended"
				body = "Unfortunately, you did not have the winning bid on {item}. Best of luck on your future bids!".format(item=self.packet["item_name"])
			elif self.note_type == "sold":
				subject = "An item you bid on has been sold"
				body = "Unfortunately, the {item} you bid on have been sold for the buy-now price set by the seller. Best of luck on your future bids!".format(item=self.packet["item_name"])
		elif message_type == "sold_item":
			if self.packet["value"] == -1:
				subject = "Your auction was closed"
				body = "An admin has closed the sale of {item} and your item has not been sold. Please contact customer service with any questions.".format(item=self.packet["item_name"])
			elif self.note_type == "auction_closed":
				subject = "The auction has closed"
				body = "The {item} you placed for auction has/have been sold for ${dollars}. You will see the funds deposited in your account in 1-3 business days.\n\nThank you for using us to sell your item! We hope you will use us again soon.".format(dollars=self.packet["value"], item=self.packet["item_name"])
			elif self.note_type == "sold":
				subject = "Your item has been sold for the buy-now price!"
				body = "The {item} you placed for sale has/have been sold for ${dollars}. You will see the funds deposited in your account in 1-3 business days.\n\nThank you for using us to sell your item! We hope you will use us again soon.".format(dollars=self.packet["value"], item=self.packet["item_name"])
		elif message_type == "closing_buyer":
			if self.packet["description"] == "day":
				subject = "An auction you are bidding on closes soon!"
				body = "The auction for {item} you bid on closes in less than 24 hours! Head back to the site to make sure you win!".format(item=self.packet["item_name"])
			elif self.packet["description"] == "hour":
				subject = "An auction you are bidding is about to close!"
				body = "The auction for {item} you bid on closes in less than one hour! Head back to the site to make sure you win!".format(item=self.packet["item_name"])
		elif message_type == "closing_seller":
			if self.packet["description"] == "day":
				subject = "An auction you created closes soon!"
				body = "The auction for {item} you created closes in less than 24 hours! Good luck!".format(item=self.packet["item_name"])
			elif self.packet["description"] == "hour":
				subject = "An auction you created is about to close!"
				body = "The auction for {item} you created closes in less than one hour! Good luck!".format(item=self.packet["item_name"])
		elif message_type == "watchlist":
			# TODO URL would be reallllllllly good here
			subject = "A new item matching your criteria was just posted!"
			body = "{item} was/were just posted on the site with a starting bid of ${dollars} in the {category} category.\n\nCome to the site to check it out!".format(dollars=self.packet["value"], item=self.packet["item_name"], category=self.packet["category"])
		return (subject, body)

	# return a dictionary where key is GUID and value is email
	def get_user_emails(self, guid_list):
		# this method calls Esther's user microservice
		reply = requests.get(self.user_url, params={"user_id":guid_list}) 
		data = reply.json()
		# Error handling
		if int(data["code"]) == 400:
			return []
		else:
			return data["email_list"]


if __name__ == '__main__':
	# defaults if args unspecified
	user_ms_host = 'dwu_user'
	user_ms_port = '36354'
	rabbithost = 'dwu_rabbit_admin'
	mongohost = 'localhost:27017'
	sendfrom = 'DWU.Main@gmail.com'

	# user microservice Ip, rabbit host, mongo host, send from email
	np = NotificationParser(user_ms_host, user_ms_port, rabbithost, mongohost, sendfrom)
