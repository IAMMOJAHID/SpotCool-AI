# Narration

The voice-over for the demo video, one section per beat of `video/storyboard.yaml`. Each `##` heading is the beat's `narration_id`, and the paragraph under it is what you say during that beat. The title beat has no narration.

How to record it (`video/README.md`, step 3):

- Read at about 140 words a minute: plain and calm, like explaining it to a colleague.
- Leave a pause of about 2 seconds between paragraphs, and none that long inside one. The assembly cuts the recording at the longest pauses and starts each paragraph at its beat; it names any paragraph it cannot place, to record again.
- Say every number as written. Each one is in `docs/release/numbers.md` with its source, and the sentence that says it names the source.
- Save the take as `video/raw/narration.wav`.

## hook

Hi. Offices are emptier now. Kastle's badge data puts US attendance at 56.1 % of its pre-pandemic level. Yet the cooling still runs on a fixed schedule, and in a typical Indian commercial building HVAC is about 55 % of the energy, says the Bureau of Energy Efficiency.

## plugs_in

Most zones have no occupancy sensor, so the BMS cools an empty room like a full one. SpotCool sits on what's already there: the cameras, the BMS, and people's complaints. No new sensors.

## world

To test it, we built a building: a BACnet BMS, cameras and occupants. Behind it, BOPTEST, an open research simulator, runs one office twice, with SpotCool and without.

## live

This is SpotCool running live. Top right, the building's cameras, here public dataset clips. Every fifteen minutes of building time, the agent sends each camera's snapshot to Meta's SAM 3.1 with one word: humans. Bottom right, every call: the model, the endpoint, Meta's request ID, what went in, and the boxes that came back with their confidence. Bottom left, those boxes on the frames. Then our code decides. An empty zone is set to unoccupied at twenty-eight degrees, at BACnet priority ten, and the building's own standby shuts its airflow off. A zone with people is left to the building, and an operator always wins. Top left, the dashboard shows each decision, and the power against the baseline.

## louvers

In the meeting room, SAM finds the person in the camera, here a recorded clip, and the agent turns the AC's louvers to follow them.

## meta_models

Meta's models see, listen and explain. SAM 3.1 makes a camera an occupancy sensor. Muse Spark reads clothing and activity, and our comfort physics sets the meeting room's setpoint. Muse Voice hears complaints. Muse Wonder answers them and narrates decisions. No language model writes to the building.

## results

SpotCool's agent used 19.94 % less HVAC energy than the baseline, and got occupancy right at every step it saw. Discomfort where people were rose from 0.33 to 2.13 kelvin hours. BOPTEST's occupant heat follows its own schedule.

## rollout

It runs on a building's own BMS and cameras, never stores a frame, and an operator always wins.

## close

SpotCool. Cooling that follows people.
