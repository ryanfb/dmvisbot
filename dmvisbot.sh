#!/bin/bash
export LC_ALL='en_US.UTF-8'
export PYTHONIOENCODING='UTF-8'
cd "$( dirname "${BASH_SOURCE[0]}" )" && python dmvisbot2.py >> /tmp/dmvisbot.log 2>&1
