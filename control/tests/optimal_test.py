"""optimal_test.py - tests for optimization based control

RMM, 17 Apr 2019 check the functionality for optimization based control.
RMM, 30 Dec 2020 convert to pytest
"""

import pytest
import warnings
import numpy as np
import scipy as sp
import control as ct
import control.optimal as opt
from control.tests.conftest import slycotonly
from numpy.lib import NumpyVersion

def test_finite_horizon_simple():
    # Define a linear system with constraints
    # Source: https://www.mpt3.org/UI/RegulationProblem

    # LTI prediction model
    sys = ct.ss2io(ct.ss([[1, 1], [0, 1]], [[1], [0.5]], np.eye(2), 0, 1))

    # State and input constraints
    constraints = [
        (sp.optimize.LinearConstraint, np.eye(3), [-5, -5, -1], [5, 5, 1]),
    ]

    # Quadratic state and input penalty
    Q = [[1, 0], [0, 1]]
    R = [[1]]
    cost = opt.quadratic_cost(sys, Q, R)

    # Set up the optimal control problem
    time = np.arange(0, 5, 1)
    x0 = [4, 0]

    # Retrieve the full open-loop predictions
    res = opt.solve_ocp(
        sys, time, x0, cost, constraints, squeeze=True)
    t, u_openloop = res.time, res.inputs
    np.testing.assert_almost_equal(
        u_openloop, [-1, -1, 0.1393, 0.3361, -5.204e-16], decimal=4)

    # Convert controller to an explicit form (not implemented yet)
    # mpc_explicit = opt.explicit_mpc();

    # Test explicit controller
    # u_explicit = mpc_explicit(x0)
    # np.testing.assert_array_almost_equal(u_openloop, u_explicit)


#
# Compare to LQR solution
#
# The next unit test is intended to confirm that a finite horizon
# optimal control problem with terminal cost set to LQR "cost to go"
# gives the same answer as LQR.  Unfortunately, it requires a discrete
# time LQR function which is not yet availbale => for now this just
# tests the interface a bit.
#
@slycotonly
def test_discrete_lqr():
    # oscillator model defined in 2D
    # Source: https://www.mpt3.org/UI/RegulationProblem
    A = [[0.5403, -0.8415], [0.8415, 0.5403]]
    B = [[-0.4597], [0.8415]]
    C = [[1, 0]]
    D = [[0]]

    # Linear discrete-time model with sample time 1
    sys = ct.ss2io(ct.ss(A, B, C, D, 1))

    # Include weights on states/inputs
    Q = np.eye(2)
    R = 1
    K, S, E = ct.lqr(A, B, Q, R)        # note: *continuous* time LQR

    # Compute the integral and terminal cost
    integral_cost = opt.quadratic_cost(sys, Q, R)
    terminal_cost = opt.quadratic_cost(sys, S, 0)

    # Formulate finite horizon MPC problem
    time = np.arange(0, 5, 1)
    x0 = np.array([1, 1])
    optctrl = opt.OptimalControlProblem(
        sys, time, integral_cost, terminal_cost=terminal_cost)
    res1 = optctrl.compute_trajectory(x0, return_states=True)

    with pytest.xfail("discrete LQR not implemented"):
        # Result should match LQR
        K, S, E = ct.dlqr(A, B, Q, R)
        lqr_sys = ct.ss2io(ct.ss(A - B @ K, B, C, D, 1))
        _, _, lqr_x = ct.input_output_response(
            lqr_sys, time, 0, x0, return_x=True)
        np.testing.assert_almost_equal(res1.states, lqr_x)

    # Add state and input constraints
    trajectory_constraints = [
        (sp.optimize.LinearConstraint, np.eye(3), [-10, -10, -1], [10, 10, 1]),
    ]

    # Re-solve
    res2 = opt.solve_ocp(
        sys, time, x0, integral_cost, constraints, terminal_cost=terminal_cost)

    # Make sure we got a different solution
    assert np.any(np.abs(res1.inputs - res2.inputs) > 0.1)

def test_mpc_iosystem():
    # model of an aircraft discretized with 0.2s sampling time
    # Source: https://www.mpt3.org/UI/RegulationProblem
    A = [[0.99, 0.01, 0.18, -0.09,   0],
         [   0, 0.94,    0,  0.29,   0],
         [   0, 0.14, 0.81,  -0.9,   0],
         [   0, -0.2,    0,  0.95,   0],
         [   0, 0.09,    0,     0, 0.9]]
    B = [[ 0.01, -0.02],
         [-0.14,     0],
         [ 0.05,  -0.2],
         [ 0.02,     0],
         [-0.01, 0]]
    C = [[0, 1, 0, 0, -1],
         [0, 0, 1, 0,  0],
         [0, 0, 0, 1,  0],
         [1, 0, 0, 0,  0]]
    model = ct.ss2io(ct.ss(A, B, C, 0, 0.2))

    # For the simulation we need the full state output
    sys = ct.ss2io(ct.ss(A, B, np.eye(5), 0, 0.2))

    # compute the steady state values for a particular value of the input
    ud = np.array([0.8, -0.3])
    xd = np.linalg.inv(np.eye(5) - A) @ B @ ud
    yd = C @ xd

    # provide constraints on the system signals
    constraints = [opt.input_range_constraint(sys, [-5, -6], [5, 6])]

    # provide penalties on the system signals
    Q = model.C.transpose() @ np.diag([10, 10, 10, 10]) @ model.C
    R = np.diag([3, 2])
    cost = opt.quadratic_cost(model, Q, R, x0=xd, u0=ud)

    # online MPC controller object is constructed with a horizon 6
    ctrl = opt.create_mpc_iosystem(
        model, np.arange(0, 6) * 0.2, cost, constraints)

    # Define an I/O system implementing model predictive control
    loop = ct.feedback(sys, ctrl, 1)

    # Choose a nearby initial condition to speed up computation
    X0 = np.hstack([xd, np.kron(ud, np.ones(6))]) * 0.99

    Nsim = 12
    tout, xout = ct.input_output_response(
        loop, np.arange(0, Nsim) * 0.2, 0, X0)

    # Make sure the system converged to the desired state
    np.testing.assert_allclose(
        xout[0:sys.nstates, -1], xd, atol=0.1, rtol=0.01)


# Test various constraint combinations; need to use a somewhat convoluted
# parametrization due to the need to define sys instead the test function
@pytest.mark.parametrize("constraint_list", [
    [(sp.optimize.LinearConstraint, np.eye(3), [-5, -5, -1], [5, 5, 1],)],
    [(opt.state_range_constraint, [-5, -5], [5, 5]),
      (opt.input_range_constraint, [-1], [1])],
    [(opt.state_range_constraint, [-5, -5], [5, 5]),
      (opt.input_poly_constraint, np.array([[1], [-1]]), [1, 1])],
    [(opt.state_poly_constraint,
      np.array([[1, 0], [0, 1], [-1, 0], [0, -1]]), [5, 5, 5, 5]),
     (opt.input_poly_constraint, np.array([[1], [-1]]), [1, 1])],
    [(sp.optimize.NonlinearConstraint,
      lambda x, u: np.array([x[0], x[1], u[0]]), [-5, -5, -1], [5, 5, 1])],
])
def test_constraint_specification(constraint_list):
    sys = ct.ss2io(ct.ss([[1, 1], [0, 1]], [[1], [0.5]], np.eye(2), 0, 1))

    """Test out different forms of constraints on a simple problem"""
    # Parse out the constraint
    constraints = []
    for constraint_setup in constraint_list:
        if constraint_setup[0] in \
           (sp.optimize.LinearConstraint, sp.optimize.NonlinearConstraint):
            # No processing required
            constraints.append(constraint_setup)
        else:
            # Call the function in the first argument to set up the constraint
            constraints.append(constraint_setup[0](sys, *constraint_setup[1:]))

    # Quadratic state and input penalty
    Q = [[1, 0], [0, 1]]
    R = [[1]]
    cost = opt.quadratic_cost(sys, Q, R)

    # Create a model predictive controller system
    time = np.arange(0, 5, 1)
    optctrl = opt.OptimalControlProblem(sys, time, cost, constraints)

    # Compute optimal control and compare against MPT3 solution
    x0 = [4, 0]
    res = optctrl.compute_trajectory(x0, squeeze=True)
    t, u_openloop = res.time, res.inputs
    np.testing.assert_almost_equal(
        u_openloop, [-1, -1, 0.1393, 0.3361, -5.204e-16], decimal=3)

@pytest.mark.parametrize("sys_args", [
    pytest.param(
        ([[1, 0], [0, 1]], np.eye(2), np.eye(2), 0, True),
        id = "discrete, no timebase"),
    pytest.param(
        ([[1, 0], [0, 1]], np.eye(2), np.eye(2), 0, 1),
        id = "discrete, dt=1"),
    pytest.param(
        (np.zeros((2,2)), np.eye(2), np.eye(2), 0),
        id = "continuous"),
])
def test_terminal_constraints(sys_args):
    """Test out the ability to handle terminal constraints"""
    # Create the system
    sys = ct.ss2io(ct.ss(*sys_args))
    
    # Shortest path to a point is a line
    Q = np.zeros((2, 2))
    R = np.eye(2)
    cost = opt.quadratic_cost(sys, Q, R)

    # Set up the terminal constraint to be the origin
    final_point = [opt.state_range_constraint(sys, [0, 0], [0, 0])]

    # Create the optimal control problem
    time = np.arange(0, 3, 1)
    optctrl = opt.OptimalControlProblem(
        sys, time, cost, terminal_constraints=final_point)

    # Find a path to the origin
    x0 = np.array([4, 3])
    res = optctrl.compute_trajectory(x0, squeeze=True, return_x=True)
    t, u1, x1 = res.time, res.inputs, res.states

    # Bug prior to SciPy 1.6 will result in incorrect results
    if NumpyVersion(sp.__version__) < '1.6.0':
        pytest.xfail("SciPy 1.6 or higher required")

    np.testing.assert_almost_equal(x1[:,-1], 0, decimal=4)

    # Make sure it is a straight line
    Tf = time[-1]
    if ct.isctime(sys):
        # Continuous time is not that accurate on the input, so just skip test
        pass
    else:
        # Final point doesn't affect cost => don't need to test
        np.testing.assert_almost_equal(
            u1[:, 0:-1],
            np.kron((-x0/Tf).reshape((2, 1)), np.ones(time.shape))[:, 0:-1])
    np.testing.assert_allclose(
        x1, np.kron(x0.reshape((2, 1)), time[::-1]/Tf), atol=0.1, rtol=0.01)

    # Impose some cost on the state, which should change the path
    Q = np.eye(2)
    R = np.eye(2) * 0.1
    cost = opt.quadratic_cost(sys, Q, R)
    optctrl = opt.OptimalControlProblem(
        sys, time, cost, terminal_constraints=final_point)

    # Turn off warning messages, since we sometimes don't get convergence
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="unable to solve", category=UserWarning)
        # Find a path to the origin
        res = optctrl.compute_trajectory(
            x0, squeeze=True, return_x=True, initial_guess=u1)
        t, u2, x2 = res.time, res.inputs, res.states

        # Not all configurations are able to converge (?)
        if res.success:
            np.testing.assert_almost_equal(x2[:,-1], 0)

            # Make sure that it is *not* a straight line path
            assert np.any(np.abs(x2 - x1) > 0.1)
            assert np.any(np.abs(u2) > 1)       # Make sure next test is useful

        # Add some bounds on the inputs
        constraints = [opt.input_range_constraint(sys, [-1, -1], [1, 1])]
        optctrl = opt.OptimalControlProblem(
            sys, time, cost, constraints, terminal_constraints=final_point)
        res = optctrl.compute_trajectory(x0, squeeze=True, return_x=True)
        t, u3, x3 = res.time, res.inputs, res.states

        # Check the answers only if we converged
        if res.success:
            np.testing.assert_almost_equal(x3[:,-1], 0, decimal=4)

            # Make sure we got a new path and didn't violate the constraints
            assert np.any(np.abs(x3 - x1) > 0.1)
            np.testing.assert_array_less(np.abs(u3), 1 + 1e-6)

    # Make sure that infeasible problems are handled sensibly
    x0 = np.array([10, 3])
    with pytest.warns(UserWarning, match="unable to solve"):
        res = optctrl.compute_trajectory(x0, squeeze=True, return_x=True)
        assert not res.success

def test_optimal_logging(capsys):
    """Test logging functions (mainly for code coverage)"""
    sys = ct.ss2io(ct.ss([[1, 1], [0, 1]], [[1], [0.5]], np.eye(2), 0, 1))

    # Set up the optimal control problem
    cost = opt.quadratic_cost(sys, 1, 1)
    state_constraint = opt.state_range_constraint(
        sys, [-np.inf, -10], [10, np.inf])
    input_constraint = opt.input_range_constraint(sys, -100, 100)
    time = np.arange(0, 3, 1)
    x0 = [-1, 1]

    # Solve it, with logging turned on
    res = opt.solve_ocp(
        sys, time, x0, cost, input_constraint, terminal_cost=cost,
        terminal_constraints=state_constraint, log=True)

    # Make sure the output has info available only with logging turned on
    captured = capsys.readouterr()
    assert captured.out.find("process time") != -1

