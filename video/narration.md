# Narration

The voice-over for the demo video, one section per beat of `video/storyboard.yaml`. Each `##` heading is the beat's `narration_id`, and the paragraph under it is what you say during that beat. The title beat has no narration.

How to record it (`video/README.md`, step 3):

- Read at about 140 words a minute: plain and calm, like explaining it to a colleague.
- Leave a pause of about 2 seconds between paragraphs, and none that long inside one. The assembly cuts the recording at the longest pauses and starts each paragraph at its beat.
- Say every number as written. Each one is in `docs/release/numbers.md` with its source, and the sentence that says it names the source.
- Save the take as `video/raw/narration.wav`.

## hook

Kastle's US badge data puts office attendance at 66.1 % on Tuesdays. Yet the cooling runs as if every desk were taken. In India, HVAC is about 55 % of a commercial building's energy, says the Bureau of Energy Efficiency. And PNNL found that counting people saves up to 17.8 %, against 5.9 % for presence alone.

## plugs_in

SpotCool adds no sensors. It uses three things a building already has. Its CCTV cameras count the people in each zone. Its BMS eases the cooling where nobody is. And complaints make sure a cold room is heard.

## world

To test it without a real building, we built one: a BACnet BMS, a VRF unit, cameras and complaints. Behind them, one BOPTEST office runs twice, once with SpotCool and once as the baseline.

## bms_yabe

Here is that BMS in YABE, a free BACnet explorer. An operator holds the core zone's cooling setpoint at priority 8. SpotCool writes at priority 10, so the operator always wins. The agent sees the hold, backs off, and says so.

## dashboard

This is the hybrid Tuesday, replayed from a saved run. People arrive, go to lunch, and meet in the afternoon. When a zone empties, its cooling setpoint goes up and its airflow shuts off. The booked meeting room is cooled half an hour before the meeting.

## louvers

In the meeting room, a phone stands in for the CCTV camera. As a person walks across the room, the agent aims the VRF unit's louvers at them, and the louvers turn smoothly to follow.

## results

With perfect perception, from a stub agent, SpotCool's policy used 19.94 % less HVAC energy than the baseline on BOPTEST's office model. Discomfort where people were rose from 0.33 to 2.13 kelvin hours. Treat it as an upper bound: BOPTEST's occupant heat also follows its own schedule, not the cameras.

## rollout

Rollout needs no new hardware: on a real building, the agent's code does not change. Frames stay in memory. The record keeps counts and decisions, never images.

## close

SpotCool. Cooling that follows people.
