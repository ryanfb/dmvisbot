#!/bin/bash
export LC_ALL='en_US.UTF-8'
export PYTHONIOENCODING='UTF-8'
export PYTHONPATH="$( dirname "${BASH_SOURCE[0]}" )/omgifol"
cd "$( dirname "${BASH_SOURCE[0]}" )" && python2 dmvisbot2.py >> /tmp/dmvisbot.log 2>&1
