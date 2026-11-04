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

Here's the codebase. The agent package is SpotCool's agent: it watches the cameras and writes to the BMS. The world package is the simulated building. They share only the contracts package, so the agent doesn't depend on the simulator. And one command starts it, with the Meta models on.

## live

This is SpotCool running live on a simulated office. Top right, the building's cameras, playing public dataset clips. Every fifteen minutes of building time, the agent sends each camera's frame to Meta's SAM 3.1, with one word: humans. Bottom right, Meta's calls as they happen: the model, the endpoint, Meta's request ID, what went in, and the boxes that came back with their confidence. Bottom left, those boxes on the frames. Top left, the floor plan and the energy, SpotCool against the baseline. Then our code decides, not a language model. An empty zone is set to unoccupied at twenty-eight degrees, at BACnet priority ten. Right now every zone is empty. Watch: people arrive at eight thirty, SAM sees them, and the agent releases those zones back to the building. An operator always wins: if anyone holds a higher priority, the agent backs off. And if the agent stops, the building takes everything back.

## louvers

In the meeting room, the camera plays a clip I filmed. SAM finds me, and the AC's louvers follow me. When someone complains it's freezing, the agent raises the setpoint.

## meta_models

Five Meta models. SAM sees people. Muse Spark reads clothing and activity, for comfort. Muse Voice hears complaints. Muse Wonder explains decisions. Muse Image draws a privacy view. No language model decides a write to the building.

## results

In simulation, on the same day, SpotCool used 19.94 % less HVAC energy than the baseline. The cost: comfort where people were went from 0.33 to 2.13 kelvin hours.

## close

SpotCool. Cooling that follows people.

## goodbye

Thank you, and goodbye.
