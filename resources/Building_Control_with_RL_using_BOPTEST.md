https://youtu.be/RTz4OIFbm6M?si=qC3BBbsyYW9jHxJf

In this video

Transcript:
0:05
hello everyone my name is jaier Aro I'm a research and development engineer at
0:11
OKO which stands for we do control you can uh always visit our website and
0:18
check out more information uh here at woo.io today we are going to walk
0:26
through this tutorial on building control withfor person learning using
0:31
the building optimization testing framework the so-called Bob test environment so what we are going to
0:38
learn with this tutorial is uh what is reinforcement learning how it works how
0:44
it can be used for the application of building energy management and we are also going to
0:50
learn uh the most famous uh it's a very popular standard for uh representing
0:57
General reinforcement learning PR problems which is called gymnasium and uh you are also going to
1:05
learn what is the Bob test framework and its gym interface uh which is very useful to
1:13
test controllers in general for uh building energy management but today we are going to have a special focus on
1:20
reinforcement learning so just a side note this tutorial is prepared for Bob
1:27
test version 0.6 and we we going to use the so-called uh Bob test service which is a service
1:34
that is prepared so as to have uh Bob test readily available without you having to install it in your laptops but
1:42
you could also work with Docker containers to spin up opas test cases uh
1:48
that work as virtual buildings and you could work with those buildings locally in your
1:53
environment so the outline is we are going to start with a brief background on uh what is for Learning and uh how
2:02
this can be applied to buildings then we're going to learn gymnasium uh which
2:08
is the standard I was talking about then Bob test and then we are going to see
2:13
how these two uh tools work together in the so called bopst gy environment and
2:20
we are going to um to do some exercises with BST gy so every time you see a
2:28
spark here that means that is a a fill-in the blank exercise so you can um
2:36
try out some uh some of the things that you've learned in the previous
2:42
sections now let's start with the the brief introduction to reinforcement
2:47
learning I should mention first um the the tutorial I will say itself
2:54
explanatory so you should be able to follow without even um the video
3:00
but uh here we are going to go explaining the main Concepts so it's even easier for you to understand so
3:09
what is reinforcement learning in the first place well this is a branch of machine learning machine learning has
3:16
three uh main branches unsupervised learning supervised learning and reinforcement learning in the first two
3:23
we learn from static data so the difference between unsupervised and supervised is whether our data has tax
3:30
so in unsupervised learning our data has no tax so it has to group the data
3:36
somehow so I think a very good example would be uh the Google photos classifier
3:42
so Google photos has not the information of who are the people that appear in
3:49
your pictures and somehow it has to figure out how to group those pictures
3:56
by people so that you can later on look for the pictures that um in which one of
4:04
your friends appear so it classifies the photos based on information without
4:10
those photos being labeled whereas in supervised learning you do have labels
4:15
of those data and you're more interested in doing things like regression but okay anyway like those
4:22
work with um those were with uh static data whereas in reinforcement learning
4:29
what we are interested in is in deriving a control policy what we want is to
4:35
learn from Dynamic data so Dynamic that we get from a process so in
4:42
reinforcement learning we have um we have what is called uh the agent and the
4:48
environment the agent is the reinforcement learning agent is the the brain is the reinforcement learning
4:54
algorithm that is going to try to learn the environment by interacting with it
5:00
empirically so it's going to send some actions and it's going to receive in return uh some rewards and observations
5:08
and from this information is going to have to uh to learn what is the optimal
5:14
uh or or the hopefully optimal uh control policy to drive this
5:19
environment so we're putting here an example in which learn uh sorry in which
5:25
an agent has to learn uh how to um grab a stick so the agent is the dog and the
5:32
dog has to learn how to grab a stick from the bment the envirment is everything surrounding the dog so that
5:38
would mean the environment is the human throwing the stick and here an observation would be the image of that
5:45
human throwing the stick and the dog has to learn that okay whenever the human is
5:51
about to throw the stick has to start running he has to grab the stick and come back and then the reward will be
5:58
for instance could be a cookie if uh the the dog has walked back with the stick
6:05
and maybe there's no cookie if the dog hasn't walked back with the stick and then the dog learns okay if I want to
6:11
get a cookie I have to do this so that's actually how reinforcement learning works and it may sound silly from this
6:17
uh simple example right but uh if you extrapolate this uh to any process any
6:24
system uh you realize this is a very powerful Concept in which we
6:30
fix or we standardize the APA API between uh the agent and the environment
6:36
and therefore with the same algorithm in the agent you can learn several environments so maybe the same algorithm
6:44
we've set here to to learn the dock to grab the stick could be used for an
6:49
industrial process uh to optimize the performance and therefore to reduce energy use or uh to reduce
6:57
costs so one important note uh is being added here is that you have to be
7:03
careful with what is called an observation and state because sometimes
7:08
it's called Uh distinctively for um for
7:14
many applications and it's not exactly the same the state is the actual uh full
7:21
representation of the environment and the observation may be a subset of that
7:29
uh repres presentation it's not always that the observation can cover the full
7:35
State um and that's always the case in continuous processes so if um if a
7:41
variable is a Continuum I cannot uh I cannot fully vectorize that uh
7:49
variable so um that's that's just side note um I'm I'm the same because that's
7:57
one of the cases um that we will have uh with buildings for instance a typical
8:02
observation is the temperature and we cannot provide a vector and observation
8:08
space with all values of uh the temperature because that's a continu an infinite amount of values that we will
8:16
need just as um curiosity a few applications of uh or a few achievements
8:25
of reinforcement learning uh in in recent times it has been able to uh
8:31
defeat the the best player of alpha go which is
8:36
a strategic strategic game and it's an special Milestone because it's a the
8:44
alpha Go game it's attributed to to need some um some creativity uh to to be
8:52
played so that that's something that's a quality that has been usually attributed only to humans and now we get that with
8:59
some for learning algorithms we can um we can learn that so that's quite
9:04
impressive so that's just a curiosity there's this um there's a link here to
9:10
um Netflix documentary which is quite nice to watch I would say so what we
9:16
have learned so far a few quick facts is that reinforcement learning is a category of machine learning that it
9:23
learns from Dynamic data so it keeps interacting with an environment and from
9:28
um from pupils of State action rewards it should be able to um to learn an
9:35
optimal control policy and to optimize that process so what all this has to do
9:41
with uh building an area management is that there's been a recent application
9:46
to this domain so if you see here in this graph um you are seeing the number
9:52
of Publications as and citations for uh optimal control in buildings during the
9:59
last decad so there's been a clear growing interest in uh learning how to
10:05
optimally control the energy of uh in buildings because we are
10:12
clearly um yeah we we are clearly under optimized uh on this Regard in sense
10:21
that we could better uh control buildings using more advanced control algorithms like predictive control or
10:29
enforcement learning so also it's also important to mention that this uh this is special
10:36
interesting since buildings in the end use one third over one/ third of the final energy use worldwide so if we are
10:44
Mis controlling this uh this processes it's a lot of energy we are wasting and
10:50
therefore we want to do better with this uh more advanced controllers that are available to us but there's a lot to do
10:55
so far we are only using root based controllers and uh therefore there's uh
11:01
this growing interest in applying this mod more modern algorithms and most of
11:09
it has been applied to um in research there's some uh that has been applied to
11:17
uh in practice but not not a lot and in research the focus has gone to demand
11:23
response settings for applying reinforcing learning to buildings uh so demand response means trying to adapt
11:29
our demand to uh to the needs of the grid so use more energy Whenever there
11:35
is more Renewables into the grid you can see here a small map on on how this
11:41
research track has evolved over time with the first uh applications of
11:47
reinforcement learning to buildings dating on 2005 2006 um here there's quite an
11:53
achievement by chadal on uh reducing the amount of data that is required to uh to
12:00
learn an agent for the application in buildings which is critical because we cannot indefinitely interact with the
12:07
building just to learn from it so we we have limited amounts of data so most of
12:13
the research goes to expedite learning so to see how we can learn with less uh
12:20
data this something we are going to see in a few moments and here it's being mentioned that this is um yeah the the
12:29
reasons why we have come back to um or or the reasons why there is so much
12:36
interest now on reinforcement learning is because of the evolution of deep learning that can uh greatly help
12:43
reinforcement learning we have way more data than before and we have uh much
12:48
more computational power than before so those are factors that are making us uh
12:55
all look into uh the new uh possibility is with reinforcement learning and here
13:01
you have some links to repositories that have somehow already considered the
13:07
possibility of implementing reinforcement learning in buildings one very interesting one is City learn which
13:13
is in a sense quite similar to what we are going to learn today uh but more
13:18
applied to um to Urban uh scale so not
13:23
to individual buildings but to districts uh in there what we want to do is to apply refor learning to to flatten the
13:30
demand of the district so uh to provide demand response in a way in your run
13:38
escape there are others like gy e+ iner gy in gy uh band different possibilities
13:46
some of them have U uh python backend for modeling the building some of them
13:52
have um an e+ uh energy plus uh back end
13:58
and uh in our case we are going to learn a bob test schim which has a backand bob
14:05
test which has uh as a very backand uh modelica to model the physics of those
14:12
buildings so I would say what sets part uh Bob test from other U Frameworks that
14:20
are used to model and simulate buildings is that with Bob test you uh have
14:26
buildings that are very reliable we model the into very detail um if you
14:32
want to know more about dop test you can just go uh to the GitHub page or uh you
14:39
could go to the to the main website of popest here where you have a full
14:45
description of the of the framework and also uh what's a list of available test
14:52
cases that you can run okay before going into Bob test let's jump into what is a gymnation
14:59
standard because that's what we are going to be um running in our uh in our
15:05
tutorial today so GM div nation is a software library library that gathers a
15:11
collection of tasks so this tasks are collect um environments so actually what
15:19
the nation is providing is a collection of environments it's not providing reinforcement learning
15:24
agents it's providing environments so will be providing a collection of uh
15:32
this site here and then it's up to the user of gnation to provide or uh to test
15:38
different reinforcement learning agents into that uh into those environments but what is very powerful of this imation is
15:45
that it's providing the standard it's providing a standard API in Python to
15:52
interact with this environments such that we can uh be sure that we can inter
15:59
act with one or many environments in the uh in the collection that they
16:05
provide um and this standard is as follows so I'm putting here like the two
16:11
main um methods that this standard um it's uh it's agreeing on which is uh the
16:20
first one is to reset the enironment so when after instantiating one environment
16:26
we'll later see how we do that we can and reset it to bring it to a logical State and in return we get the
16:32
observation that very first observation before starting with that environment and uh the second one is once we get the
16:41
observation we expect our environment sorry our agent to um to derive an an
16:49
action from that observation and send the action to the environment so once we get the observation we compute our
16:56
action and we step the environment with that action and in return we are going
17:02
to get again the next observation but this time also uh the reward from going
17:07
from the previous observation to the new observation and also whether the uh the
17:13
uh the environment is terminated or truncated terminated means the goal of
17:18
the environment has been reached so we naturally terminate that environment or
17:24
uh we truncate it so we finish it before we we uh we reach the end of the episode
17:32
of the environment um because um yeah because we want to to
17:37
finish before because maybe we are not interested in the last uh part or in the last steps because we we are wandering
17:45
around from what is the interesting data we want to uh to grasp from the
17:50
environment and then there is an optional information uh return argument
17:56
as well which is just to provide any information about how uh the
18:02
environment has evolved now um something I should mention and there's a couple of things
18:08
here to add is that um gymnasium relies very much in
18:13
the concept of episodes of experience so uh the idea is that we reset to the
18:18
beginning of an episode our environment and then we interact for that episode and then we reset again to another
18:26
episode so it's can happen but it's rare to see environments that are Continuum
18:33
um that just have one long episode instead we typically want to have like
18:38
fractions we uh and we jump from one fraction to the other with the reset
18:44
method and another thing I wanted to say is that gymnation is providing a
18:49
standard so it's not that we can use only the collection from um that the The
18:57
Collection that they provide I'm going to um going to show you here so if we
19:03
click in the gymnation here you see their collection different uh types of
19:08
environments but since they provide the standard you can also build your own um
19:16
environments and learn reinforcement learning to those environments and that's exactly what we are going to do
19:22
with bopst gim so we are going to see here how we can use uh the the gnation
19:29
standard to interact with buildings of the Bob framework hopefully that'll uh
19:34
be more clear in a few minutes okay for start we're just going to um we're just
19:41
going to start installing uh [Music]
19:48
gymnasium so first time we on S it may take bit long and for Star we are just going to
19:56
learn uh how to interact with one of the environments that gymnasium provides
20:04
which is the card pole
20:09
example okay so it's installing gnation okay it
20:14
has been installed already um so the carpo example is basically uh a pendulum that we want to
20:23
keep directed by applying forces at the card on on the bottom so uh it's a
20:28
friction L card uh we can apply Force to the right or the left and uh the idea is
20:35
that you receive a reward of one every time a step that you keep the pendulum
20:41
upright so um just to get familiar with
20:47
how gymnation Works uh first thing you need to do is to import gyum to make the
20:52
card pole environment and then we decide that the maximum number of episodes we want to run is 10
20:59
and while uh in in every episode why while this is episode is not done we
21:06
keep uh applying an action that in this case just for the sake of illustration
21:12
we are going to get to be random so in uh dnation environments typically you
21:18
can just use this so once the environment is instantiated uh you can access the
21:24
action space and you can sample the action space so this is just going to
21:29
a random action in the format that is expected for that environment
21:36
sorry so um with that action we can just
21:41
uh input that action to the environment and and step with that action and then
21:46
we will receive the the observation reward terminated truncated info that I was talking about before then we compute
21:53
whether the the episode is done either because has been terminated or truncated
21:58
and then we step one reward sorry we step uh forward uh and add the reward uh
22:04
in case we've gone one because we have still survived during this control step
22:09
with the pendulum upright and then we are pring for um all those episodes uh
22:15
for those 10 episodes we are printing what is the total reward we've gotten we
22:21
may not expect to get a lot of rewards since we are just applying random actions to the pendulum so this uh this
22:28
is a random thing where we will be getting and you see that for every reward sorry for every episode we get a
22:34
different reward depending how much or how long this has last so if we were to compute this again we will get a
22:41
different amount for every episode because it's just it's just random now the first fill in blank
22:47
exercise is is for you to warm up so um in in this exercise what I'd like you to
22:56
do is that you you have the same exercise as before so the the same
23:01
environment the the pendulum and you will see here a blank that you need to
23:07
fill in order to compute a control algorithm that is a bit more smart than
23:13
just applying a random uh control action so you can see already here I'm
23:20
extracting for you uh the observation space so the observation space is um has
23:27
a dimension of four uh first value is the card position then
23:32
the card velocity and then we have angular coordinates so we have the pole
23:38
angle and we have the pole velocity and then we have the same test for uh
23:45
running the environment as before the idea is that here you compose some
23:51
control logic that de say decides on the the force on the direction of the force based on one of these um observations I
23:59
can advise you to use the pole angle and here you see if um what what means to
24:07
apply an action to the left or to the right so if you you take action zero that means the force will go to the left
24:14
if you take action one the force will go to the right and uh you can decide on
24:20
that based just on the whatever the angle is uh positive of or uh
24:26
negative so if you imp that all right and also you need to add here a line to
24:33
use that control algorithm if you implement that all right you should expect these um Total Rewards per
24:41
episode of experience to last in aage longer than just applying a random
24:47
control action so I'll leave that to you hopefully um hopefully you can play
24:53
around and learn a bit how to work with gymnasium but uh I'll
24:58
uh myself go straight to the next section to the next part of the tutorial
25:04
which is learning what is the billing optimization testing framework so the
25:09
main use case of Bob test uh I'm going to explain that with this figure is one
25:15
of a control developer that wants to implement a controller for building energy management and for whatever
25:23
reason um cannot interact directly with the building or just because is cheaper
25:28
to interact with simulations than with real buildings wants to test uh his or
25:33
her controller in simulation so bobes provides two main things it provides a
25:40
suite A menu of building emulators that they are very detailed very reliable and
25:47
also it provides a runtime environment that can simulate those buildings so the
25:54
idea is that the user picks one of those emulators and deploys that along with the runtime environment in a Docker
26:02
container either in the cloud as we are going to do or in your uh Docker um
26:09
diamond and your local host and then it can run Echo simulation sending control
26:14
actions and receiving measurements from that virtual building so uh after some interaction
26:21
after um an episode of experience as we were calling before uh we we can also
26:28
get a report from uh the runtime environment as to uh know what are the
26:34
main kpis of uh of those uh of that interaction of that control
26:41
logic so um the the uh test case is basically a
26:49
what is So-Cal a functional mockup unit which is a model encapsulated into a
26:55
file which follows a standard the functional mockup interface standard for simulation um which is basically a
27:03
detail building um sorry a detail building model then uh for every emulator you
27:11
also have boundary condition data so you have uh weather data pricing occupancy
27:17
schedules that come along with the model and uh yeah that are compiled all
27:25
together with the model and then you also get an API for interacting with uh
27:32
with bobes such that it's standardized the way you uh send control signals and
27:37
receive control signals so you can see here if we click there on the link you can see what are the commands that you
27:43
can run so here you have commands to uh get available test cases to to start
27:50
test case to initialize a test case to uh get what are the measurements that we
27:57
can read from the building the inputs uh to get the results after the simulation
28:02
to get key performance indicators up to the point that we have simulated and so on you see there are not as many and the
28:11
the idea here is to have uh the least
28:16
possible amount of API calls that allow full freedom to uh do anything you need
28:24
uh with the building so uh um the the basic workflow
28:30
that we have is that we select a test case we uh select one of the testing scenarios which are standardized
28:37
typically we have Peak heating period scenario typical heating period Peak cooling period typical cooling period
28:44
those are pre-computed uh based on the data of that building that we are working with
28:51
and um yeah they last for two weeks so that's a standardized so we can compare
28:57
uh the different uh results for different scenarios but it's not mandatory to run
29:05
a simulation in one of the scenarios you could run outside of the scenarios just to learn a policy uh or a control policy
29:13
to train a gray box model for instance for different reasons you may want to be
29:18
outside of the testing scenarios then you set the parameters for uh C simulation so what is the control step
29:25
length that we want to interact with uh what is the forecast Horizon if we want to request uh weather forecast for
29:33
instance every control step for predictive controllers this kind of things um run the test case scenario in
29:39
a c simulation after you have set the parameters and then evaluate so we just call the API that was saying get kpis
29:47
and that will give you an evaluation okay um as I was saying
29:54
before you can see here the repository of currently available uh test cases you
29:59
can see here small description um and if you go uh below
30:06
you can also see the time periods what are the electricity price profiles available that you can also set through
30:12
the API if you want to see a full uh description of the test case you can
30:18
just jump into do that link and then you have uh data about the building envelope
30:23
about the hedback system design about what is the the control included in the
30:30
building um what are the the points that you can read that you can overwrite so
30:35
what what are those values that you can play with what is the forecast that you could get if you needed
30:41
to and uh here also the scenario info in more detail than in the previous
30:47
website so an important thing I didn't mention about bopas is that it always
30:54
comes along with the Baseline controller which is meant to be representative of the building we are working with so that
31:00
means that I can decide either to overwrite one of the control points or I
31:07
can just run the simulation in free floating and without overriding anything
31:13
and then it will give me still some output that follows the rule based controller that is implemented in that
31:20
building that is what is So-Cal the Baseline controller so that is also something
31:26
that is mentioned in the documentation for every emulator so here you can see
31:33
uh H back system design and then here rule base or local Loop controllers if included it says but
31:41
it's always included so you always have a rule based controller uh that is
31:46
called uh Baseline controller so you can just um gather data from that Baseline
31:53
control if you're not interested in exciting the building for getting more R data for instance you could just and the
31:58
Baseline controller and get um get the default uh default control uh behavior
32:05
let's say so now we are first going to
32:10
connect for uh with a test case of uh Bob test this is the so-called best
32:16
hydronic heat pump case which is actually the one I was showing you before uh what you can see here is the
32:22
the URL to bopta service so bopta service um is readily available in the
32:28
cloud to spin one of the uh test cases in a Docker container so you can connect
32:34
to it through um uh through this URL here and just select the test case you
32:42
want and that what is going to do is just to spin a Docker container with the test case there on the cloud but it
32:49
Remains the Same as or almost the same Remains the Same as if you were to
32:54
deploy it locally in your laptop so I I said almost the same because of a couple
33:00
of things like if you deploy it locally in your laptop of course it's going to be faster but you need a bit of um
33:07
knowledge on Docker and you need to have Docker installed locally and it's going to be faster of course because you don't
33:13
have to interact with the with the cloud but um and and it has almost the same
33:20
API so all API calls are exactly the same except for the fact that we have to
33:26
put here um the sorry the the URL and the test uh ID
33:35
so in if you were deploying it locally you could just use uh the the URL and
33:40
that would work but since we are working with the cloud we need a unique identifier to know what is our specific
33:47
test case there in the cloud anyways um here you have in Bob test uh you have
33:55
documentation to uh no better how to interact with it locally or remotely if
34:00
you wanted to I want this to point you to one of uh the tabs here here in
34:06
training you have more documentation how to interact with BP test cases so hopefully that's useful but for us today
34:13
we are going to work with uh service version so we just go for it uh run the
34:20
first cell we get our test case we uh we
34:25
get the the name of the test case just to prove that we have connected to it and indeed this is returning the name to
34:34
be best hit pump case which is expected because it's the one we have selected so now we are connected to one of the test
34:42
cases which I should explain before moving forward because this is going to be the one we are going to use
34:47
throughout the tutorial uh to be using throughout the tutorial so this is a very simple uh
34:54
building it's a residential dwelling um it's modeled as a single zone and it has
35:01
floor heating so the floor heating is connected to a air to water heat pump
35:07
and uh the controls that are implemented are basic thermostatic controls so it
35:13
basically reads the temperature of the zone and this C1 is going to activate
35:19
the heating so the the motor of the the heat pump of the compression of the heat pump in case heating is required uh
35:27
using a pi and then uh this controller two is basically going to activate the
35:34
circulation pump of the emission system so floor heating and also the the fan in
35:40
the evaporator of the heat pump in case uh this motor is activated so there's
35:45
like first layer of control and second layer and what is Handy of this
35:50
controller is that we can easily um control the the the test test
35:57
case just by overriding the modulation of the heat pump which is basically what this C1 is decided is deciding so this
36:05
is C1 this is the controll one basically it's a pi controller that is say it's on
36:10
this uh override uh heat pump modulation uh why and we can just uh ourselves
36:18
decide to override this directly externally and then um all uh other
36:24
control will follow as required because this C2 is the slave of C1 it's going to
36:31
uh follow or it's going to activate whatever is needed if heating is required if you uh or the modulation of
36:39
the heat pump is um higher than zero so what I'm uh trying to say is
36:47
there is one control action which is the modulation of the heat pump that goes from zero to one zero means no hitting
36:54
one means full hitting now um there could be other
37:02
possibilities like we want to get if we want to get information about what we
37:07
can uh override in this uh in this building and what we can measure we can
37:13
just request to get the inputs and the measurements and that will return a full
37:18
list uh which is also available sorry in the in the list of
37:25
test cases here so here in the documentation you always have the full
37:30
list of inputs so actions that you can overwrite and outputs that you can read
37:36
with a description now uh since we have learned
37:43
how to use Bob test or how to interact with it uh there is a very first
37:49
exercise on the usage of Bob test where uh you have to create your own
37:56
controller it's a very simple controller uh being um that I'm asking to here it's
38:03
a proportional controller so the idea is that you uh fill this Gap here to
38:10
compute control based on a proportional setting so we have a set point that we
38:15
want to reach and then we have a proportional gain and every control step we are going
38:22
to receive a measurement Y and we want to base on that measurement we want to
38:30
decide on the value so if you are below the set point you may want to increase
38:35
the value and if you are above the set point you want to decrease the value so
38:42
there is some um default values here you could use or you could tune those but
38:47
what matters here is that you control this control logic it's very simple with
38:52
if else statement should uh be enough now the the next cell is to test your
38:59
controller the next cell is quite long but you shouldn't be concerned about it
39:04
because it's just some plotting functionality here and uh the C simulation Loop so here what we are
39:11
going to do is to sorry here what we are going to do is to
39:18
um initialize the scenario so we are choosing the peak heating period and the
39:24
electricity pricing to be dynamic so uh uh it's it's that means that there is a
39:30
profile for electricity pricing that goes uh from low to high uh pricing
39:36
every day um and then you also have here the start time days which you get from
39:42
the return of the setting the scenario and then we set the step the counter
39:48
step to be one hour so every hour we are deciding on uh on that uh control action
39:55
we apply that action and then we wait for 1 hour and then we repeat the process we decide one control action and
40:01
we implement it and we apply that for 24 hours here this is just to uh print on
40:09
the loop what is the the reading of the temperature of the zone and uh here also
40:15
what is the time the simulation time so you can know what's going on and it's
40:21
only at the end that we are going to use the plotting functionality of this method I mentioned before and and we are
40:28
going to plot it so um if you implement that you should see something similar to
40:34
this not exactly the same because it may depend on your controller so what you can see in the plot are uh the Comfort
40:42
bounds that are uh set in bopas which you see are baring because it depends on
40:47
whether the house is occupied or unoccupied that you want to have broader
40:52
or a um more tight uh comfort range and
40:58
then you see in Orange the evolution of the temperature of the zone now uh here below you see the heat
41:05
pum modulation signal that is set by the controller and it's clear that whenever
41:10
we turn it on we increase uh temperature if we turn it off temperature decreases
41:16
of course because we are in the bck heating period so temperature outside as you can see at the bottom plot it's
41:24
quite low and last line you can see is the jellow line which is the solar IR
41:30
radiation so feel free to play around as much as you like implement the controller and have a lot of fun um and
41:39
then after you finish you could just uh stop the test case um just not to
41:44
overwhelm the server in case there are too many uh test cases spinning okay now let's go to uh Bob The
41:54
Sim we have learned what is gym what is gymnation and we have learned what is Bob test uh so as you can um Envision as
42:03
you can guess bobm is a framework that merges both of them basically what it's doing is accommodating the API of Bob
42:11
test to the standard of gimnasium so we can use the the bobas emulators directly
42:20
with the gym standard so we can uh directly do sorry
42:27
um we can directly call the reset and the step method and that is going to uh
42:34
work with bopus gim now uh for uh working with bopus gim we need to
42:41
install this um stable baselines and uh numpy
42:47
one the specific versions and uh here I'm going to explain a bit uh how it works so this is
42:56
what I have just explained um the the particular hyperparameter
43:03
that is important when we initialize an environment with popam is the test case because that one is going to point to uh
43:09
the test case that we want to run with and then the the state of the emulator
43:14
or of the environment it can have a Time component uh so we can read the time if
43:22
that was interesting for the environment or for the agent and then uh it has a
43:27
set of exogenous components so those measurements or forecasting variables if
43:33
we needed to like ambient temperature or what is the the current temperature in
43:39
the zone and then for the actions we you can have all the um uh all the variables
43:47
that you can input to the emulator so when we reset the environment we are
43:52
going to warm it up to the beginning of one of uh the scenarios or we are going
43:57
to warm it up to any time uh throughout the year depending on what we choose and
44:04
then the step method is going to uh send the action to the environment and that
44:11
action is going to be sent to Bob test sorry uh it seems I need to restart
44:19
session for some reason yeah sometimes this happens
44:25
um happens one running through Chrome not through Firefox for some reason
44:31
otherwise I think it's resolved this by restarting
44:37
um and uh it sends the action to the environment and um simulates and you get
44:45
the back you get back the observation space uh again and also the reward so it
44:53
works just as a as a regular uh uh regular uh gym
45:00
environment now uh what we want to do is to clone uh bobas gy so just get a copy
45:08
of the of the repository of the bobas gy
45:13
service version and from there we are going to import uh we are going to
45:18
import bopst gym and we are going to instantiate our first uh bobus gy
45:24
environment as you can see here the main hyper parameter is this best hydronic uh
45:31
heat pump case then we decide the action space to be the modulation of the heat
45:38
pump with this list here then we uh we decide we design the
45:45
observation space to be this um variable here which is the temperature of the zone and what follows is just a two pole
45:54
explaining or showing what the expected minimum and maximum value of that uh
46:00
variable this is just for normalization purposes uh that's why for observations
46:06
we require uh dictionary instead of for um instead as uh in actions that what we
46:13
have is a list because the actions um that the minimum maximum
46:20
value of the actions can be derived from the Bob test framework itself but that's not the case for the observations so we
46:26
need to Pro provide that information now if we decide to start with the random start time that means
46:32
every time we uh we reset um uh an episode we are going to
46:39
randomly allocate the emulator in any time of the year and otherwise uh we are
46:45
going to decide on a start time and then every time we restart the environment we are going to start here so this is the
46:52
time of the year in seconds um that means we are starting the 31st day of
46:57
the year after that 31st day so that's the 1 of February then the maximum episode length
47:03
so we won the episode to finish after maximum 24 hours and the warm-up period
47:09
we want the episode to warm up so the the emulator to run with the Baseline controller for 24 hours right before the
47:17
beginning of the episode to bring our emulator to a logical initial State and
47:23
the step period to be 1 hour as before so that's uh what I have explained right now here you have a view
47:32
on how the whole Machinery works so uh this is just for illustrating what
47:37
you're doing with um B gim at the very left hand side uh I'm going to explain
47:43
this very quickly but at the left hand side you have yourself the user who decides to through the reinforcement
47:49
learning agent to um to interact with an
47:55
environment and then you have um the gym environment where you can uh
48:01
send to which you can send the reset and step methods uh which are the most common to be sent and after that uh Bob
48:10
theim what is going to do is that he's going to accommodate those commands to the commands of the Bob test so uh if we
48:17
reset what we are going to do is basically to initialize and to get forecast if needed to return State and
48:23
then in the episode Loop every time we step we are going to call Advance well not we but B team is going to call
48:29
Advance get foras if needed get kpis if needed and then we are going to compute
48:35
the reward and whether the episode is done and return the state and
48:40
reward so as we have Set uh first thing we want to do with every gym EP every
48:46
gym environment is to reset it to bring it to logical state so if we call here
48:52
and reset we are going to reset it according to the specifications we had
48:58
uh defined when we deployed or when we um how to call it when we uh
49:04
instanciated the environment that's word so we only have one observation so
49:10
that's the temperature that we want to transform to from celvin to Celsius and we see that if we reset it for the first
49:17
uh day of February we get that after one day of warmer period our s temperature
49:22
is 21° and we can see that in fact the first first um the start time of the
49:30
environment is 31st day of the year if we translate that from seconds to uh
49:36
sorry from yeah from seconds to days notice that for every environment we can print what is the observation and the
49:42
action space you can see here Box means a continuous environment uh of only one
49:49
dimension and uh we have an observation space that is uh continuous uh because
49:56
it's temperature and these are the minimum maximum values expected that we have said before in the
50:03
when we instantiated environment and for the action it's a again a continuous that goes from zero to one because
50:09
that's a modulation of the heat pump now we could print the environment
50:15
and there is some printing functionality here that will display a lot of information about uh Bob test and uh the
50:22
Bob test GM environment so first Bob test case information and then sorry and
50:27
then at the bottom here some other information more specific to the gym environment so that
50:35
could uh be handy and what is very important is that b gym comes with some
50:40
functionality to discretize and normalize which is very handy uh because we can learn faster if we discretize our
50:49
environment so we here what we are doing we are importing the discretized action
50:55
wrapper so that we transform uh the action environment uh sorry the action
51:01
space of the environment to be uh instead of continuous to have this one bin so two possible actions so we have
51:09
transformed to be discrete of Dimension two and for
51:15
um we can always access the unrra environment if we want it and you can see that in that case we print the
51:22
action space we get that this still uh continuous
51:28
now the first fill in- the blank exercise for Bob tum is to implement a
51:33
similar controller than the one that you had implemented before uh for uh for
51:40
bobas where we were using directly the uh the step method and the the API of
51:47
Bob test in general we want to use now um um the the terminology of bobst gym
51:55
or gymnas in general you can see already that here there is something that could
52:01
seem counterintuitive which is that the method to uh to request the control
52:08
action is not control it's called predict and that's because I want you to
52:13
get you yourselves familiar already with um with the gymnasium standard in the
52:20
gymnation standard an agent when you require an agent to return you with an action based Bas on observation you
52:28
don't uh you don't use the method control you use the method predict because the agent is typically a Model A
52:34
Nur Network deep um a deep um Nur network uh probably with um
52:43
that is going to return you somehow that action set that's why it's called predict but the the logic that you
52:48
should Implement here is very similar in this case we have the set as an attribute of our simple controller and
52:56
we want to implement uh a logic that returns an action based on that uh based on the
53:03
observation so here we read the observation if observation which is the temperature of the zone as we have
53:09
defined it it's below a value then we apply an action that is going to warm up
53:15
the building and the other way around uh basically we can go from zero to one so either we decide to be zero or one so
53:24
here you have the C simulation Loop that you can Implement uh here you don't have to touch anything but hopefully on the
53:31
go you can see how your um control logic works so we are going
53:38
to be printing all the time the uh temperature in the zone now we get to the most juicy part
53:48
of the of the tutorial where we are going to go beyond implementing um
53:55
implementing as BAS basic rule based control action in gymnasium for bobst we
54:01
are going to try to implement our own reinforcement learning agent so this is
54:09
this is going quite deep into reinforcement learning and I'm just going to explain the intuition to it if
54:16
you need further information you always can access the more detailed explanation
54:22
um that you get here at the bottom so this is a more formal uh formal description of how uh
54:30
reinforcement learning works if you get lost and also here there's some references to uh books and also other
54:38
materials for understanding how reinforcement learning works but I'm going to try to quickly explain how we
54:44
are going to develop this reinforcement learning agent we are going to use the
54:49
socalled uh Q learning algorithm and we are going to um formulate therefore our
54:58
problem as a markup decision process so for every state of our building we apply
55:04
an action and then we receive in return a reward and a next action so Q learning
55:11
basically is based on boot strapping to represent what is the value of being in
55:18
a specific State and taking specific action so it's going to say that our
55:27
um the value of being uh to state as an action a and take an action a from there
55:33
is the reward that we obtain plus a discounted um the discounted maximum of
55:41
the next uh uh the Q value in the next state and next action so that's what is
55:48
called bootstrapping is the uh belma optimality equation and is useful to
55:53
formulate the problem in a way that we can learn uh very
55:59
quickly okay so this will be for instance a representation of what I'm trying to explain here which is the uh
56:06
the Q function of whatever uh process uh and here we can see that it's just we
56:14
have just one dimension for the observation and for the action space just for illustration purposes so the
56:22
cube function will tell us that if we are in state s and we come uh we want to decide on an action
56:28
basically we want to take this action because it's the one that has the highest value the highest
56:34
qsa value now the question comes that's great as far as we have the the Q
56:42
function but how do we get to that Q function well what we are going to do is
56:47
we are going to use this formula here so we are going to keep updating uh Q based
56:53
on empirical interactions so uh we are going to start with an
56:59
initialized Q uh it could be a flattened faction and then every now and then
57:06
let's say every uh few steps of uh experience with the environment we are going to uh update uh
57:16
that Quee with what is called the temporal difference so the difference between what is my current value at
57:23
qsa and the value that we empirically observed this is something that we are getting from the environment so we get
57:32
uh the reward and we can know what is the maximum value of Q because we we already have that estimate it's true in
57:39
the beginning is going to be very bad because it's just an estimate maybe it's flat so this is not saying much but with
57:44
time it will be sapen as to resemble what is reality so we keep interacting
57:53
and and saping our uh function until it gets more and more accurate and once we
57:58
have so we uh have something like this so that we can decide on the action to
58:04
be taken now there is another thing uh that
58:09
is very relevant to reinforcement learning which is the exploration exploitation dilemma it's uh coming to
58:15
say like okay imagine now I have a well Saed function um do I want to take the
58:24
action that leads to the most EXP expected cumulative reward which is this one and keep applying that action always
58:31
so do I want to follow the policy that I have learned or do I want to explore
58:37
what is the possibility with other actions that maybe I didn't learn and could lead to a higher expected
58:44
cumulative return so the the answer to that is it's not uh straightforward it's
58:51
a in the end a trade-off that you have to make you have to exploit because you
58:56
want to um to optimize your process but you have to keep exploring somehow uh
59:03
applying some random actions so a typical approach that is being used for
59:08
this is the Epsilon gritty algorithm where we start um applying always um
59:17
random action um so the probability of the greedy action in the very beginning is
59:23
very low so we we take uh random actions in the very beginning and then we keep
59:28
decreasing over time that probability of random actions until we are more
59:34
confident we have learned a policy that works that is good enough and at some point we just apply very small random
59:41
actions once in a while um but uh this could go to zero as well but it's
59:48
usually better to to keep some chances of exploring uh after uh a
59:55
period so all this I explained with words it's implemented here in this Q
1:00:01
learning agent um you can go through it to understand I'm not going to go into
1:00:07
the details hopefully it's self-explanatory with what I have said um just going to say there are two
1:00:14
methods one uh to one to learn and in which you can set the the number of
1:00:20
episodes you you want to learn uh and then another one to uh to predict so if
1:00:27
you just want to an action with an Epsilon grey policy after having learned
1:00:33
the the uh the environment you can just use the the predict method now I'm going
1:00:41
to um explain how to apply this to Bob test gy so the idea in Bob test is this
1:00:50
that we want to uh reduce thermal discomfort so the years of time we are
1:00:57
outside a Comfort Range by applying some power no this power is driven uh in our
1:01:03
example before by uh the by the modulation of the heat pump so we want
1:01:11
to Define um a reward function in our
1:01:16
environment that tries to minimize what is uh the discomfort and energ use so
1:01:24
this is something we can do do with the functionality of Basim we just have to inherit the Basim environment and change
1:01:31
the get reward method that's a standard um so get reward method comes along uh
1:01:37
sorry the b gym environment comes along with the a default get reward method but
1:01:43
here we want to customize it and we are going to use a kpi get kpi code to get
1:01:50
all kpis and from that one we are going to use the thermal discomfort um so so
1:01:56
we are going to inspect we are going to analyze whether the integrant of that
1:02:02
discom for has uh increased over time if we detect that it has increased sorry if
1:02:10
we detect it has not increased if it's equal than the previous objective integrant then we give a reward to our
1:02:16
atent of one if we detect it has increased then we don't give any
1:02:23
reward Okay so uh that's the way we Define the reward
1:02:28
which is a very important step when uh when defining uh the algorithm for
1:02:36
reinforcement learning now what we are going to do is that we are going to instantiate that
1:02:42
bobas gy uh custom reward okay NP is not defined um because yeah of course I
1:02:50
didn't import it because I didn't run the exercise so NP as
1:03:01
MP okay uh I need uh to use some part of
1:03:08
your exercise
1:03:17
so um name is not defined it's in
1:03:26
why name is not all right here we need to find
1:03:36
this okay so once we have defined our environment with the custom reward we
1:03:42
instantiate it so we do as before uh action spaces the modulation observation
1:03:50
space is the temperature of the Zone we want to discretize it uh here
1:03:56
I'm explaining uh how to discretize the observation space so there there are
1:04:02
different ways of doing so depending on whether you want the values outside the bins to be part of the observation space
1:04:10
or not in this case we want so basically what we are defining is an observation space of Dimension three which is going
1:04:17
to tell us whether our temperature is below the confor set point in the comfor
1:04:24
range or above the confor set point so uh if we are here we are good if we are
1:04:31
here we are too hot if we are here we are too cold basically that's what it means and now we are going to learn oh I
1:04:38
didn't Define uh I didn't call the cell here that defines our Q learning
1:04:47
agent now I'm going to learn the uh here this I already called
1:04:56
for 10 episodes of experience so uh here I'm um what I'm going to do is to
1:05:05
interact for uh 10 episodes that I have defined here to
1:05:10
be of two days yes two days and uh so
1:05:16
for every episode we are going once every episode is done we are going to get um to get a print
1:05:26
that displays the evolution of the temperatures so I didn't mention but diation environments typically allow to
1:05:31
render the evolution of learning so that that's very handy and in that way you
1:05:36
can see whether your environment is doing something useful or not so here we
1:05:43
get the first episode you can see that it has been randomly initialized here
1:05:51
because we have decided to well we have set it uh to be rendered as I was was saying and we Define a random start time
1:05:59
to be true so it just happened that it initialized our building to start this
1:06:05
day I think it's 12th of January okay it's applying some random actions to it
1:06:13
forward it's every time getting some uh some rewards so the reward you can see here
1:06:22
it's uh one if we remain inside the comfor range that we have
1:06:28
defined or it's um uh or it's uh zero so there is no
1:06:35
reward in case we are outside for whatever reason so this values here are
1:06:44
going to sape the uh fun our Q function
1:06:49
which in our case as in the graph I was showing above is just one dimension
1:06:55
because we have just one dimensional observation space which is our temperature and one dimensional action
1:07:01
space which is our uh our modulation of the heat pump and every time I'm
1:07:07
printing here what is the Q value of in in specific State and taking specific
1:07:14
action so what you can see here is that for instance for uh observation
1:07:22
three observation three if we here are uh too
1:07:28
worm um I believe yeah that's the case because that that's the only uh when we
1:07:34
are too warm that's the only case we have experienced here if we
1:07:40
apply uh no action so no activated heat pump that has some value whereas if
1:07:48
we uh if we apply um uh the heat pump modul to be one that
1:07:56
has no value so it has less value than activating which makes sense um so that
1:08:01
way it keeps learning and and you can see also since it didn't have lower discomfort it has no nothing to say
1:08:08
about that case of experience so it will keep
1:08:14
learning it's already in um episode number five but on the meantime I'm
1:08:20
going to explain it should go for 10 episodes and
1:08:25
but on the meantime I'm going to explain how we can interpret this so I'm just
1:08:30
plotting here what is uh I'm trying to uh display what is the Q function so
1:08:38
you can see it so this is related to what I was saying before uh you can see that uh if we are for instance too cold
1:08:47
after we have learned these episodes we can see that the agent has learned that it should uh that it has more value to
1:08:54
apply uh action one so modulation of the hit pum full power then just not activating
1:09:01
it and if we are to worm is the opposite we prefer not to activate R uh sorry we
1:09:07
prefer not to activate than activating it whereas if we are in between if we're at the comfor range it it doesn't really
1:09:15
distinguish whether it's better or not because it's not as smart because we have provided with very little data
1:09:21
actually the reason why we are learning this is because we want to Showcase or to
1:09:27
demonstrate how reinfor learning agent can learn something uh from data right
1:09:32
but but actually this is very silly this is something you could have learned by
1:09:37
your uh or you have could have learned by your own but you seeing here how the
1:09:43
agent can uh has some learning capabilities here I'm just explaining
1:09:48
that uh the value function is what is called the projection of the of the
1:09:54
state function in the uh State space not uh it has no two Dimension so not State
1:10:01
action space but only in the state space so you can see that the most value is to be in uh the Comfort
1:10:09
Range and um and here what you can do is to test
1:10:16
your uh algorithm so I'm not going to run it just yet because it's still
1:10:21
learning in the episode 78 okay um so you can uh basically once you
1:10:29
have learned instead of using the learn method you can use the predict method with the terministic equals true to
1:10:35
exploit the policy that you have learned and if you do so you will see that it
1:10:41
will be using the Q function here as to decide on the action so every time uh
1:10:48
you you get an act temperature below uh 21° is going to uh start the the heat
1:10:57
pump at maximum power so the last fill in- the blank exercise which is what we are going to
1:11:03
finish with is for you to improve on the learning so you can try out several
1:11:10
different things actually this is something is not set in stone there's not a specific answer to it uh but I
1:11:18
gives here some recommendations you could for instance tune the reward to return a reward of minus one instead of
1:11:25
of zero uh in case you want to penalize uh whenever you are outside of the
1:11:30
Comfort Range you can also tune Epsilon Min Epsilon uh Decay Alpha and Gamma so
1:11:37
the hyperparameters of your Q learning agent and uh you can also uh try out
1:11:45
other things like increase the State observation space like for instance of
1:11:50
using just um the discretized action wrapper with um one bin you can use 10
1:11:57
bins so different uh to have more granularity on the action that you can
1:12:02
take for the heat pump or uh you can augment the observation space with things like Aman temperature solar
1:12:10
radiation internal gase electricity price and even make those uh predictive so I'm inviting you here to uh fill out
1:12:19
that observation space that is more complete and you could you could use
1:12:26
the uh yeah the information that you gathered when interacting with Bob test
1:12:31
that you requested to get the inputs and measurements and then you just have to normalize and discretize observation and
1:12:38
action space as we were doing before with as many bins as you like then just
1:12:44
instantiate your reinforcement learning agent here I'm leaving that blank in case you want to use more advanced uh
1:12:51
reinforcement learning agent examp for instance from stable Baseline and after that you may want to learn uh
1:13:00
at least for uh just to check that you can actually learn from some uh episodes
1:13:07
or time steps of experience uh just to check that all the formulation you're
1:13:13
are deploying here it's correct um but what it will be nice is
1:13:18
that you make sure that you at least uh print what is the observation action space that you get with your uh new
1:13:26
environment okay so hopefully this has finished already
1:13:32
um yeah this is this is done has learned with the T 10 episodes so we now would
1:13:38
be able to run also all this um all
1:13:43
these cells that we didn't run before so that's it thank you very much for uh
1:13:49
your attention I'm going to stop sing and stop the recording and I hope you
1:13:55
have a lot of fun uh with Bob test and Bob test bye