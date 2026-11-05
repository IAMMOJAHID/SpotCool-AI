# Narration

The voice-over for the demo video, one section per beat of `video/storyboard.yaml`. Each `##` heading is the beat's `narration_id`, and the paragraph under it is what you say during that beat. The title beat has no narration.

The words are what the person on camera says: `scripts/video/prep_clips.py` cuts the takes, and each beat's `talk` plays them.

## intro

Before moving forward, let me clarify. We built the entire project during the hackathon, and nothing was out before this hackathon.

## story

In commercial buildings across hot climates, up to 70 % of electricity goes directly into HVAC cooling. Why? Because traditional HVAC controllers rely on a static wall sensor, and they have no idea where humans are sitting, what they are wearing, and how they actually feel. So they blindly cool empty space. Meet our project, SpotCool AI, an autonomous HVAC controller layer powered by five Meta AI models. Instead of ripping and replacing hardware, SpotCool AI sits right on top of a building's existing BMS controller, combining existing CCTV feeds with thermal sensors to deliver targeted 3D spot cooling and real-time energy optimization.

## whiteboard

This is a comprehensive architecture that we have built. This is the agent, and this is the virtual building that we have built using the BOPTEST framework with the HVAC system. This agent is taking the current state of the building with the previous state, and then outputting a manipulated variable, and this is applied to the HVAC system of the building. And again, this process is continuously repeating.

## code

Now, I'm going to explain our codebase and how we have integrated the Meta models. So, this is the code for SAM 3.1. SAM 3.1 is the eyes: it takes a camera snapshot and the prompt, humans, and outputs a bounding box with a confidence score. We filter the detections below 0.3 and calculate the occupant count. This count drives the building's occupancy control and the meeting room's airflow direction. Now, this is our code for Muse Spark. It receives a camera's image, an occupant count and a structured prompt. It outputs information about clothing, activity, posture and room conditions. Now, this is the code for Muse Voice. It is like ears: it converts in-memory audio into complaint text, which is combined with typed feedback. So, this is the code for Muse Wonder. It classifies a complaint into cold, hot, draft or stuffy. Deterministic rules adjust the temperature and fan speed, while Wonder generates a grounded occupant response and a dashboard explanation. This is, finally, our Muse Image, which creates privacy-preserving, faceless room illustrations. We overlay live occupancy boxes and counts for visualization, without affecting HVAC control.

## command

This is the command to start and run our codebase, so I'm now going to run the codebase.

## dash_a

So, here our code is running, and we are visualizing what is happening inside the building. So, here you can see, in West, two people are going, and it's tracking them, creating the bounding box around them. Now, here in the terminal you can see what model we are calling, and this is the request ID. Also, there is an endpoint here.

## dash_b

In this video, this is actually me. I have recorded myself and fit it into one of the rooms. So, you can see here I'm at one position, and the AC's louver angle is forty-five degrees. Now, I'll move from this place to this place. Now, the AC's louver angle moves to another degree, to fit on me and to fix the airflow towards me.
