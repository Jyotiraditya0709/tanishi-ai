"""The frontier map: what she can and cannot do (E3).

    from tanishi.frontier import graph, clustering
    g = graph.CapabilityGraph()          # tables live in the Core State db
    g.seed()                             # the 12 seed capabilities
    clustering.nightly(g, steps)         # failure clusters no node explains become `missing` candidates
"""
