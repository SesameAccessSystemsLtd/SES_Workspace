#!/bin/bash
# Daily Picking & Booking-in Morning Review (Richard)
# Weekday 08:00, Purchasing thread. Runs the read-only Asana scanner via the workspace venv.
cd "C:/Users/GilesHugo/SES_Workspace" || exit 2
exec "C:/Users/GilesHugo/SES_Workspace/.venv/Scripts/python.exe" \
  "skills/purchasing/reviews/daily_picking_booking_review.py"
