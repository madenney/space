from pyphysx import Scene, RigidDynamic, Material
scene = Scene()
rock = RigidDynamic()
rock.set_mass(10.0)
rock.set_global_position([0, 0, 5])
scene.add_actor(rock)
ground = RigidDynamic()
ground.set_global_position([0, 0, 0])
scene.add_actor(ground)
for _ in range(120):  # 2 sec at 60 FPS
    scene.simulate(1/60)