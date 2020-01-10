A dockerized notifications microservice purpose-built for an auction site. Ingests asynchronously from RabbitMQ feed. Stores necessary data in Mongo.


Usage instructions follow.

--------------

# LOAD THE IMAGE

docker load < dwu_rmq_admin.tar.gz
docker load < dwu_notifications_microservice.tar.gz

# RUN RABBIT ADMIN CONTAINER
# Do this first in a separate shell
docker run -d --hostname dwu-wabbit --name dwu_rabbit_admin -p 15672:15672 -p 5672:5672 dwu_rmq_admin

# RUN NOTIFICATIONS MICROSERVICE
docker run -di --hostname dwu-consumer --link dwu_rabbit_admin --name dwu_notifications dwu_notifications_microservice

# REBOOT DB DATA
docker exec -w="/src" -i dwu_notifications mongorestore dump/ 

# NOTE: arguments are positional and currently optional based on default values 
# You can see the defaults in 'notifications.py'
# To run default:
docker exec -w="/src" -i dwu_notifications python3 notifications.py

# To run with arguments:
docker exec -w="/src" -i dwu_notifications python3 notifications.py <USER-MS IP:PORT> <RABBITHOST> <MONGOHOST> <SENDFROM>

# You can also always go interactive if you need to:
docker exec -it dwu_notifications /bin/bash

# TO SAVE NEW MONGO DATA BEFORE STOPPING CONTAINER
docker exec -w="/src" -1 dwu_notifications mongodump --uri="mongodb://localhost:27017"

# THAT'S ALL! Your're ready to start sending notifications!
