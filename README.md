# Mapping-Tool
The purpose of this project is to simulate deployment of communication network relays in real world.

The network consists of a central gateway and allows to define data collection points or coverage regions.
Algorithm then connects all necessary points and/or regions to the gateway, ensuring network redundancy but also minimizing the required nodes.

FILE DESCRIPTION:
- Data - folder, with a dummy txt. It can be deleted later. Information about relays, areas, gateway and mesh is stored in this folder.
- Templates/map.html - file to generate an interactable map in the browser
- config.py - contains configuration variables and functions
- map_server.py - file to handle interactions between user and the map, like define the coordinated of gateways, nodes and generate mesh
- mesh_solver.py - this script contains logic to generate an optimized mesh topology. Handle changes with care, especially when switching map service provider.                    It is recommended to run one iteration to see how the data about points is saved in json in data folder.

To use the tool first set the gateway. This will be your central point.
Later you need to select from the ledt menu either shapes or points, representing required coverage. You need to save shapes in the top menu after this.
With gateway set and shapes saved, you can generate mesh to visualize the optimal deployment
